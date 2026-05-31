"""
Bob Shop (bobshop.co.za) scraper — formerly bidorbuy.co.za, rebranded in 2024.
Uses httpx + BeautifulSoup. Listings are embedded as JSON inside
<product-card><script type="application/json">{...}</script></product-card>
web components, so no CSS selector for cards is needed.

URL pattern fix (2025-05):
  OLD domain: bidorbuy.co.za  → 301 redirect (all pages returned 404 after redirect)
  OLD path:   /listing/search.htm?q={query}&p={page}
  NEW domain: bobshop.co.za
  NEW URLs:
    Page 1:   https://www.bobshop.co.za/search/{query}
    Page N>1: https://www.bobshop.co.za/mobilejquery/jsp/tradesearch/TradeSearch.jsp
              ?submitter=SearchUrlRewrite&IncludedKeywords={query_plus}&pageNo={N}

Card parsing fix:
  OLD: CSS selectors (.search-item, .listing-item, etc.) — all 0 results
  NEW: <product-card> custom elements each containing
       <script type="application/json">{title, amount, url, location, images, openTime, ...}</script>
"""

import re
import json
import asyncio
from datetime import datetime, timezone
from typing import AsyncGenerator
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from loguru import logger

BASE_URL = "https://www.bobshop.co.za"

# Pagination endpoint (used for pages 2+)
_SEARCH_PAGE_URL = (
    f"{BASE_URL}/mobilejquery/jsp/tradesearch/TradeSearch.jsp"
    "?submitter=SearchUrlRewrite"
    "&IncludedKeywords={keywords}"
    "&pageNo={page}"
)

SEARCH_QUERIES = [
    "used golf clubs",
    "golf irons",
    "golf driver",
    "golf bag",
    "golf shoes",
    "callaway golf",
    "taylormade golf",
    "titleist golf",
    "ping golf",
    "golf attire",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-ZA,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _build_url(query: str, page: int) -> str:
    """Return the correct Bob Shop search URL for a query and page number."""
    if page == 1:
        # Clean URL form — used for first page
        slug = query.replace(" ", "+")
        return f"{BASE_URL}/search/{slug}"
    # Paginated form for pages 2+
    keywords = quote_plus(query.replace(" ", "+"))
    return _SEARCH_PAGE_URL.format(keywords=keywords, page=page)


def _parse_cards(soup: BeautifulSoup) -> list[dict]:
    """
    Extract listing data from <product-card> web components.
    Each component embeds a JSON blob in <script type="application/json">.
    Returns a list of raw data dicts.
    """
    results = []
    for pc in soup.find_all("product-card"):
        script_tag = pc.find("script", type="application/json")
        if not script_tag or not script_tag.string:
            continue
        try:
            data = json.loads(script_tag.string)
            results.append(data)
        except (json.JSONDecodeError, ValueError):
            continue
    return results


async def scrape_bidorbuy(
    max_pages: int = 3,
    delay: float = 2.0,
) -> AsyncGenerator[dict, None]:
    """
    Async generator yielding listing dicts from bobshop.co.za
    (formerly bidorbuy.co.za).
    """
    seen_urls: set[str] = set()

    async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        for query in SEARCH_QUERIES:
            for page in range(1, max_pages + 1):
                url = _build_url(query, page)
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        logger.warning(f"BobShop {resp.status_code} for '{query}' page {page}")
                        break

                    soup = BeautifulSoup(resp.text, "html.parser")
                    cards = _parse_cards(soup)

                    if not cards:
                        logger.debug(f"BobShop no cards for '{query}' page {page}")
                        break

                    for data in cards:
                        try:
                            title = data.get("title", "").strip()
                            full_url = data.get("url", "")
                            if not full_url.startswith("http"):
                                full_url = BASE_URL + full_url

                            if full_url in seen_urls or not title:
                                continue
                            seen_urls.add(full_url)

                            # Price — stored as a float in 'amount'
                            price_zar: float | None = None
                            amount = data.get("amount")
                            if amount is not None:
                                try:
                                    price_zar = float(amount)
                                except (TypeError, ValueError):
                                    pass

                            # Location
                            location = data.get("location", "")

                            # Image — prefer thumbnail, fall back to full image
                            image_url = ""
                            images = data.get("images", [])
                            if images:
                                first_img = images[0]
                                image_url = (
                                    first_img.get("thumbnail")
                                    or first_img.get("thumbnail_small")
                                    or first_img.get("image")
                                    or ""
                                )

                            # Posted date — use openTime (ISO 8601 string)
                            posted_at = datetime.utcnow()
                            open_time = data.get("openTime", "")
                            if open_time:
                                try:
                                    posted_at = datetime.fromisoformat(
                                        open_time.replace("Z", "+00:00")
                                    ).replace(tzinfo=None)
                                except ValueError:
                                    pass

                            # Condition as description substitute
                            condition = data.get("condition", "")
                            description = f"Condition: {condition}" if condition else ""

                            yield {
                                "platform":    "bidorbuy",
                                "url":         full_url,
                                "title":       title,
                                "price_zar":   price_zar,
                                "description": description,
                                "location":    location,
                                "image_url":   image_url,
                                "posted_at":   posted_at,
                            }

                        except Exception as e:
                            logger.warning(f"BobShop card parse error: {e}")
                            continue

                    logger.info(f"BobShop '{query}' page {page}: {len(cards)} cards")
                    await asyncio.sleep(delay)

                except httpx.RequestError as e:
                    logger.error(f"BobShop request failed: {e}")
                    await asyncio.sleep(delay * 2)
                    break
