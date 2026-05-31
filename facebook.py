"""
Facebook Marketplace scraper — uses Playwright (headless Chromium)
because the page is fully JS-rendered.

IMPORTANT: Facebook heavily rate-limits scrapers. This scraper uses:
  - Random delays between actions
  - Realistic mouse movements + scrolling
  - A persistent browser profile to retain cookies

You must log in to Facebook manually the FIRST time:
  python scrapers/facebook.py --login

After that, the saved profile keeps you logged in for future runs.
"""

import asyncio
import json
import re
import os
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from playwright.async_api import async_playwright, Page
from loguru import logger

PROFILE_DIR = Path("data/fb_profile")
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

SEARCH_QUERIES = [
    "golf clubs",
    "golf irons",
    "golf bag",
    "golf driver",
    "golf shoes",
    "callaway golf",
    "taylormade golf",
    "titleist",
    "ping golf",
    "golf polo shirt",
]

LOCATION = "South+Africa"

def parse_price(text: str) -> float | None:
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", "").replace(" ", ""))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


async def human_scroll(page: Page, times: int = 4, delay: float = 1.2):
    """Scroll down slowly like a human would."""
    for _ in range(times):
        await page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
        await asyncio.sleep(delay + (0.3 * (_ % 2)))


async def scrape_facebook_marketplace(
    max_per_query: int = 20,
    delay: float = 3.0,
    headless: bool = True,
) -> AsyncGenerator[dict, None]:
    """
    Async generator yielding listing dicts from Facebook Marketplace.
    Uses a saved browser profile to stay logged in.
    """
    seen_urls: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
            viewport={"width": 1280, "height": 900},
            locale="en-ZA",
            timezone_id="Africa/Johannesburg",
        )

        page = browser.pages[0] if browser.pages else await browser.new_page()

        # Check if we're logged in
        await page.goto("https://www.facebook.com/marketplace/", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        if "login" in page.url.lower() or await page.query_selector('[data-testid="royal_login_button"]'):
            logger.warning(
                "Not logged in to Facebook. Run with --login flag first.\n"
                "  python scrapers/facebook.py --login"
            )
            await browser.close()
            return

        for query in SEARCH_QUERIES:
            search_url = (
                f"https://www.facebook.com/marketplace/search?"
                f"query={query.replace(' ', '%20')}"
                f"&location={LOCATION}"
                f"&exact=false"
            )

            try:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(delay)
                await human_scroll(page, times=5, delay=1.0)
                await asyncio.sleep(1.5)

                # Collect listing cards via Playwright selectors
                cards = await page.query_selector_all('[data-testid="marketplace_feed_item"]')

                # Fallback: grab all links that look like marketplace listings
                if not cards:
                    all_links = await page.query_selector_all("a[href*='/marketplace/item/']")
                    logger.debug(f"Facebook '{query}': fallback link harvest — {len(all_links)} links")

                    for link in all_links[:max_per_query]:
                        try:
                            href = await link.get_attribute("href")
                            if not href:
                                continue
                            full_url = "https://www.facebook.com" + href if href.startswith("/") else href
                            # Strip query params
                            full_url = full_url.split("?")[0]

                            if full_url in seen_urls:
                                continue
                            seen_urls.add(full_url)

                            # Title from aria-label or text content
                            title = await link.get_attribute("aria-label") or ""
                            if not title:
                                title = (await link.inner_text()).strip()[:120]

                            # Price from nearby text
                            price_text = ""
                            try:
                                parent = await link.evaluate_handle("el => el.closest('div[style]')")
                                if parent:
                                    price_text = await parent.evaluate("el => el.innerText")
                            except Exception:
                                pass

                            price_match = re.search(r"R\s*[\d\s,]+", price_text or "")
                            price_zar = parse_price(price_match.group()) if price_match else None

                            yield {
                                "platform":    "facebook_marketplace",
                                "url":         full_url,
                                "title":       title,
                                "price_zar":   price_zar,
                                "description": "",
                                "location":    "South Africa",
                                "image_url":   "",
                                "posted_at":   datetime.utcnow(),
                            }

                        except Exception as e:
                            logger.warning(f"Facebook link parse error: {e}")
                            continue

                else:
                    count = 0
                    for card in cards:
                        if count >= max_per_query:
                            break
                        try:
                            link_el = await card.query_selector("a")
                            href = await link_el.get_attribute("href") if link_el else ""
                            full_url = (
                                "https://www.facebook.com" + href
                                if href and href.startswith("/") else href
                            )
                            full_url = (full_url or "").split("?")[0]

                            if full_url in seen_urls or not full_url:
                                continue
                            seen_urls.add(full_url)

                            title = await card.evaluate("el => el.innerText") or ""
                            title = title.split("\n")[0][:120]

                            price_match = re.search(r"R\s*[\d\s,]+", title)
                            price_zar = parse_price(price_match.group()) if price_match else None

                            img_el = await card.query_selector("img")
                            image_url = await img_el.get_attribute("src") if img_el else ""

                            yield {
                                "platform":    "facebook_marketplace",
                                "url":         full_url,
                                "title":       title,
                                "price_zar":   price_zar,
                                "description": "",
                                "location":    "South Africa",
                                "image_url":   image_url or "",
                                "posted_at":   datetime.utcnow(),
                            }
                            count += 1

                        except Exception as e:
                            logger.warning(f"Facebook card error: {e}")

                logger.info(f"Facebook Marketplace '{query}': harvested {len(seen_urls)} so far")
                await asyncio.sleep(delay)

            except Exception as e:
                logger.error(f"Facebook page error for '{query}': {e}")
                await asyncio.sleep(delay * 2)

        await browser.close()


async def interactive_login():
    """Open a visible browser so you can log in manually. Run once."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
        )
        page = browser.pages[0]
        await page.goto("https://www.facebook.com/login/")
        logger.info("Log in to Facebook in the browser window. Press ENTER here when done.")
        input()
        await browser.close()
        logger.success("Profile saved. Future scrape runs will stay logged in.")


if __name__ == "__main__":
    import sys
    if "--login" in sys.argv:
        asyncio.run(interactive_login())
    else:
        async def _test():
            async for listing in scrape_facebook_marketplace(max_per_query=5):
                print(listing)
        asyncio.run(_test())
