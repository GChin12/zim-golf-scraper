"""
Gumtree SA scraper — searches golf-related listings across
sports > golf category. Uses httpx + BeautifulSoup (no JS needed).

URL pattern fix (2025-05):
  OLD (caused infinite redirect loop https->http->https):
      /q/{query}/?isSearchFormSuggestionsOpen=false&q={query}&page={n}
  NEW (works, confirmed live):
      Page 1:  /s-{query}/v1q0p1
      Page N:  /s-{query}/page-{N}/v1q0p{N}

Card selector fix:
  OLD: li.listing-result / article.listing-result / .view.natural li  (all 0 results)
  NEW: span.related-item  (20 cards per page)
      title:   a.related-ad-title
      price:   span.ad-price
      location: span.location
      image:   img.lazyload[data-src]
      desc:    .description-container
"""

import re
import asyncio
from datetime import datetime
from typing import AsyncGenerator

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from scorer import score_listing
from database import Listing

BASE_URL = "https://www.gumtree.co.za"

SEARCH_QUERIES = [
    "golf clubs used",
    "golf irons used",
    "golf bag",
    "golf driver used",
    "golf attire",
    "golf shoes",
    "titleist used",
    "callaway used",
    "taylormade used",
    "ping irons used",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-ZA,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def parse_price(text: str) -> float | None:
    """Extract numeric price from strings like 'R 1 500' or 'R1500.00'."""
    cleaned = re.sub(r"[^\d.]", "", text.replace(" ", ""))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _build_url(query: str, page: int) -> str:
    """
    Build the correct Gumtree search URL for a given query and page number.
    Gumtree SA uses /s-{query}/v1q0p1 for page 1 and
    /s-{query}/page-{N}/v1q0p{N} for subsequent pages.
    """
    slug = query.replace(" ", "+")
    if page == 1:
        return f"{BASE_URL}/s-{slug}/v1q0p1"
    return f"{BASE_URL}/s-{slug}/page-{page}/v1q0p{page}"


async def scrape_gumtree(
    max_pages: int = 3,
    delay: float = 2.5,
) -> AsyncGenerator[dict, None]:
    """
    Async generator yielding raw listing dicts from Gumtree SA.
    Iterates over all SEARCH_QUERIES, up to max_pages each.
    """
    seen_urls: set[str] = set()

    async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        for query in SEARCH_QUERIES:
            for page in range(1, max_pages + 1):
                url = _build_url(query, page)
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        logger.warning(f"Gumtree {resp.status_code} on page {page} for '{query}'")
                        break

                    soup = BeautifulSoup(resp.text, "html.parser")

                    # Each listing card is a <span class="related-item ...">
                    cards = soup.select("span.related-item")

                    if not cards:
                        logger.debug(f"No cards found for '{query}' page {page} — stopping")
                        break

                    for card in cards:
                        try:
                            # Title — <a class="related-ad-title" href="..."><span>Title</span></a>
                            title_el = card.select_one("a.related-ad-title")
                            title = title_el.get_text(strip=True) if title_el else ""

                            # URL
                            href = title_el["href"] if title_el else ""
                            full_url = href if href.startswith("http") else BASE_URL + href

                            if full_url in seen_urls or not title:
                                continue
                            seen_urls.add(full_url)

                            # Price — <span class="ad-price">R 1,500</span>
                            price_el = card.select_one("span.ad-price")
                            price_text = price_el.get_text(strip=True) if price_el else ""
                            price_zar = parse_price(price_text)

                            # Location — <span class="location"><i ...></i><span>Other</span></span>
                            loc_el = card.select_one("span.location")
                            if loc_el:
                                # Strip the icon text, keep only the inner span text
                                inner = loc_el.select_one("span")
                                location = inner.get_text(strip=True) if inner else loc_el.get_text(strip=True)
                            else:
                                location = card.get("data-brevo-location", "")

                            # Image — <img class="lazyload" data-src="...">
                            img_el = card.select_one("img.lazyload")
                            image_url = ""
                            if img_el:
                                image_url = (
                                    img_el.get("data-src")
                                    or img_el.get("src")
                                    or ""
                                )

                            # Description snippet — <div class="description-container"><span>...</span></div>
                            desc_el = card.select_one(".description-container")
                            description = desc_el.get_text(strip=True) if desc_el else ""

                            yield {
                                "platform":    "gumtree",
                                "url":         full_url,
                                "title":       title,
                                "price_zar":   price_zar,
                                "description": description,
                                "location":    location,
                                "image_url":   image_url,
                                "posted_at":   datetime.utcnow(),
                            }

                        except Exception as e:
                            logger.warning(f"Gumtree card parse error: {e}")
                            continue

                    logger.info(f"Gumtree '{query}' page {page}: {len(cards)} cards")
                    await asyncio.sleep(delay)

                except httpx.RequestError as e:
                    logger.error(f"Gumtree request failed for '{query}': {e}")
                    await asyncio.sleep(delay * 2)
                    break
