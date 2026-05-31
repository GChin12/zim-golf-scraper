"""
OLX South Africa scraper — PLATFORM DEFUNCT, repurposed to Bob Shop (used items).

OLX SA SHUTDOWN NOTICE (verified 2025-05):
  olx.co.za now serves a static "OLX South Africa is no longer available" page.
  All requests redirect to https://www.olx.co.za (homepage) which returns a
  sunset notice pointing users to AutoTrader and Property24.
  The original CSS selectors (li[data-aut-id='itemBox'], .EIR5N, ul.b9mTt li,
  [class*='item']) all return 0 results because there is no search content.

REPLACEMENT STRATEGY:
  This scraper has been repurposed to scrape Bob Shop (bobshop.co.za — formerly
  bidorbuy.co.za) filtered to SECOND_HAND condition only. This is complementary
  to bidorbuy.py which scrapes all conditions. Together they maximise used-gear
  coverage from the SA marketplace while the platform key "olx" is preserved
  in the DB to avoid breaking schema/reports.

  URL pattern used:
    https://www.bobshop.co.za/mobilejquery/jsp/tradesearch/TradeSearch.jsp
    ?submitter=SearchUrlRewrite&IncludedKeywords={query}&Condition=SECOND_HAND&pageNo={N}

  Cards are <product-card> web components with embedded JSON blobs.
"""

import re
import json
import asyncio
from datetime import datetime
from typing import AsyncGenerator
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from loguru import logger

BASE_URL = "https://www.bobshop.co.za"

# Paginated search endpoint with used-only condition filter
_SEARCH_URL = (
    f"{BASE_URL}/mobilejquery/jsp/tradesearch/TradeSearch.jsp"
    "?submitter=SearchUrlRewrite"
    "&IncludedKeywords={keywords}"
    "&Condition=SECOND_HAND"
    "&pageNo={page}"
)

SEARCH_QUERIES = [
    "golf clubs",
    "golf irons",
    "golf bag",
    "golf driver",
    "golf shoes",
    "callaway",
    "taylormade",
    "titleist",
    "ping golf",
    "golf equipment",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-ZA,en;q=0.9",
}


def _build_url(query: str, page: int) -> str:
    keywords = quote_plus(query.replace(" ", "+"))
    return _SEARCH_URL.format(keywords=keywords, page=page)


def _parse_cards(soup: BeautifulSoup) -> list[dict]:
    """
    Extract listing data from <product-card> web components.
    Each component contains <script type="application/json">{...}</script>.
    """
    results = []
    for pc in soup.find_all("product-card"):
        script_tag = pc.find("script", type="application/json")
        if not script_tag or not script_tag.string:
            continue
        try:
            results.append(json.loads(script_tag.string))
        except (json.JSONDecodeError, ValueError):
            continue
    return results


async def scrape_olx(
    max_pages: int = 3,
    delay: float = 2.0,
) -> AsyncGenerator[dict, None]:
    """
    Async generator yielding listing dicts.

    NOTE: OLX SA is permanently shut down. This function now scrapes
    Bob Shop (bobshop.co.za) second-hand items to maintain coverage of
    used SA golf equipment. The platform key in yielded dicts is kept as
    "olx" to avoid breaking existing DB records and reports.
    """
    seen_urls: set[str] = set()

    async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        for query in SEARCH_QUERIES:
            for page in range(1, max_pages + 1):
                url = _build_url(query, page)
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        logger.warning(f"BobShop(olx) {resp.status_code} for '{query}' page {page}")
                        break

                    soup = BeautifulSoup(resp.text, "html.parser")
                    cards = _parse_cards(soup)

                    if not cards:
                        logger.debug(f"BobShop(olx) no cards for '{query}' page {page}")
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

                            # Price
                            price_zar: float | None = None
                            amount = data.get("amount")
                            if amount is not None:
                                try:
                                    price_zar = float(amount)
                                except (TypeError, ValueError):
                                    pass

                            # Location
                            location = data.get("location", "")

                            # Image
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

                            # Posted date
                            posted_at = datetime.utcnow()
                            open_time = data.get("openTime", "")
                            if open_time:
                                try:
                                    posted_at = datetime.fromisoformat(
                                        open_time.replace("Z", "+00:00")
                                    ).replace(tzinfo=None)
                                except ValueError:
                                    pass

                            condition = data.get("condition", "")
                            description = f"Condition: {condition}" if condition else ""

                            yield {
                                "platform":    "olx",
                                "url":         full_url,
                                "title":       title,
                                "price_zar":   price_zar,
                                "description": description,
                                "location":    location,
                                "image_url":   image_url,
                                "posted_at":   posted_at,
                            }

                        except Exception as e:
                            logger.warning(f"BobShop(olx) card parse error: {e}")
                            continue

                    logger.info(f"BobShop(olx) '{query}' page {page}: {len(cards)} cards")
                    await asyncio.sleep(delay)

                except httpx.RequestError as e:
                    logger.error(f"BobShop(olx) request failed for '{query}': {e}")
                    await asyncio.sleep(delay * 2)
                    break
