"""
Main scraper orchestrator.

Usage:
  python main.py                  # Run all scrapers once
  python main.py --scheduler      # Run on a schedule (every 5 hours)
  python main.py --platform gumtree   # Run a single platform
  python main.py --report         # Print top deals from DB
"""

import asyncio
import argparse
import sys
import os
from datetime import datetime
from pathlib import Path

from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from database import init_db, Listing
from scorer import score_listing
from gumtree import scrape_gumtree
from bidorbuy import scrape_bidorbuy
from olx import scrape_olx

# ── Logging setup ─────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
logger.add("logs/scraper_{time:YYYY-MM-DD}.log", rotation="1 day", retention="7 days", level="INFO")

# ── DB setup ──────────────────────────────────────────────────────────────────
DB_URL = os.getenv("DATABASE_URL", "sqlite:///data/golf_deals.db")
Path("data").mkdir(exist_ok=True)
engine, SessionFactory = init_db(DB_URL)


# ── Core save logic ───────────────────────────────────────────────────────────

def save_listing(raw: dict, db: Session) -> Listing | None:
    """Score and persist a raw listing dict. Returns None if duplicate."""
    url_hash = Listing.make_hash(raw["url"])

    # Dedup check
    existing = db.query(Listing).filter_by(url_hash=url_hash).first()
    if existing:
        return None

    breakdown, brand, condition, category = score_listing(
        title=raw.get("title", ""),
        description=raw.get("description", ""),
        price_zar=raw.get("price_zar"),
    )

    listing = Listing(
        url_hash    = url_hash,
        url         = raw["url"],
        platform    = raw["platform"],
        title       = raw.get("title", ""),
        price_zar   = raw.get("price_zar"),
        condition   = condition,
        brand       = brand,
        category    = category,
        description = raw.get("description", ""),
        location    = raw.get("location", ""),
        image_url   = raw.get("image_url", ""),
        posted_at   = raw.get("posted_at") or datetime.utcnow(),
        deal_score  = breakdown.total,
        is_hot_deal = breakdown.total >= 65,
    )
    db.add(listing)
    db.commit()
    return listing


# ── Platform runners ──────────────────────────────────────────────────────────

async def run_gumtree(db: Session):
    new, hot = 0, 0
    async for raw in scrape_gumtree(max_pages=3, delay=2.5):
        listing = save_listing(raw, db)
        if listing:
            new += 1
            if listing.is_hot_deal:
                hot += 1
    logger.success(f"Gumtree done — {new} new listings, {hot} hot deals")
    return new, hot


async def run_bidorbuy(db: Session):
    new, hot = 0, 0
    async for raw in scrape_bidorbuy(max_pages=3, delay=2.0):
        listing = save_listing(raw, db)
        if listing:
            new += 1
            if listing.is_hot_deal:
                hot += 1
    logger.success(f"BidOrBuy done — {new} new listings, {hot} hot deals")
    return new, hot


async def run_olx(db: Session):
    new, hot = 0, 0
    async for raw in scrape_olx(max_pages=3, delay=2.0):
        listing = save_listing(raw, db)
        if listing:
            new += 1
            if listing.is_hot_deal:
                hot += 1
    logger.success(f"OLX done — {new} new listings, {hot} hot deals")
    return new, hot


# ── Full run ──────────────────────────────────────────────────────────────────

PLATFORM_MAP = {
    "gumtree":   run_gumtree,
    "bidorbuy":  run_bidorbuy,
    "olx":       run_olx,
}


async def run_all(platform: str | None = None):
    db = SessionFactory()
    try:
        start = datetime.utcnow()
        logger.info(f"=== Scrape run started at {start.isoformat()} ===")

        runners = (
            {platform: PLATFORM_MAP[platform]}
            if platform and platform in PLATFORM_MAP
            else PLATFORM_MAP
        )

        total_new = total_hot = 0
        for name, runner in runners.items():
            logger.info(f"--- Running {name} ---")
            try:
                new, hot = await runner(db)
                total_new += new
                total_hot += hot
            except Exception as e:
                logger.error(f"{name} scraper crashed: {e}")

        elapsed = (datetime.utcnow() - start).seconds
        logger.success(
            f"=== Run complete in {elapsed}s — "
            f"{total_new} new listings, {total_hot} hot deals ==="
        )
    finally:
        db.close()


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(limit: int = 20):
    db = SessionFactory()
    try:
        deals = (
            db.query(Listing)
            .filter(Listing.active == True)
            .order_by(Listing.deal_score.desc())
            .limit(limit)
            .all()
        )
        print(f"\n{'='*70}")
        print(f"  TOP {limit} GOLF DEALS  |  {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
        print(f"{'='*70}")
        for i, d in enumerate(deals, 1):
            flag = "🔥" if d.is_hot_deal else "  "
            price = f"R{d.price_zar:,.0f}" if d.price_zar else "POA"
            print(
                f"{flag} #{i:02d}  [{d.deal_score:3d}] "
                f"[{d.platform:<18}] "
                f"{price:>8}  "
                f"{d.brand:<14} "
                f"{d.category:<8}  "
                f"{d.title[:42]}"
            )
            print(f"         {d.url}")
        total = db.query(Listing).count()
        hot   = db.query(Listing).filter(Listing.is_hot_deal == True).count()
        print(f"\n  Total in DB: {total}  |  Hot deals: {hot}")
        print(f"{'='*70}\n")
    finally:
        db.close()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Zim Golf Deal Scraper")
    parser.add_argument("--scheduler", action="store_true", help="Run on a 5-hour schedule")
    parser.add_argument("--platform", choices=list(PLATFORM_MAP.keys()), help="Run one platform only")
    parser.add_argument("--report", action="store_true", help="Print top deals from DB and exit")
    args = parser.parse_args()

    if args.report:
        print_report()
        return

    if args.scheduler:
        scheduler = AsyncIOScheduler()
        scheduler.add_job(run_all, "interval", hours=5, kwargs={"platform": args.platform})
        scheduler.start()
        logger.info("Scheduler started — running every 5 hours. Press Ctrl+C to stop.")
        try:
            asyncio.get_event_loop().run_forever()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler stopped.")
    else:
        asyncio.run(run_all(platform=args.platform))
        print_report(limit=10)


if __name__ == "__main__":
    main()
