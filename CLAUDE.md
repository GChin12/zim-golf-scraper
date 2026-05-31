# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Python-based async web scraper targeting South African golf equipment marketplaces (Gumtree, Bid or Buy, OLX, Facebook Marketplace). Listings are scored on resale value for the Zimbabwe market and stored in SQLite.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

Facebook requires a one-time manual login (stored in `data/fb_profile/`):
```bash
python facebook.py --login
```

## Common Commands

```bash
python main.py                          # Run all scrapers once
python main.py --platform gumtree       # Single platform: gumtree | bidorbuy | olx
python main.py --scheduler              # Auto-run every 5 hours
python main.py --report                 # Print top 20 deals by score
```

Individual scrapers can also be run directly (`python gumtree.py`, etc.) to print raw listings to stdout without saving.

## Architecture

**Data flow:** Scrapers → Scorer → Database → Report

- [main.py](main.py) — CLI entry point (argparse), `save_listing()` orchestrates dedup + scoring + persistence, `AsyncIOScheduler` runs every 5 hours
- [database.py](database.py) — Single `Listing` SQLAlchemy model; dedup via SHA256 URL hash; `init_db()` returns `(engine, SessionFactory)`; set `DATABASE_URL` env var to swap SQLite for PostgreSQL
- [scorer.py](scorer.py) — Scores 0–100: brand tier (A/B/C → +30/+15/+5), condition (+0–40), price-to-market ratio (+5–25), Zimbabwe demand items (+20); ≥65 = hot deal, 50–64 = manual review, <50 = ignored
- [gumtree.py](gumtree.py), [bidorbuy.py](bidorbuy.py), [olx.py](olx.py) — httpx + BeautifulSoup async generators; static HTML scraping
- [facebook.py](facebook.py) — Playwright async generator; persistent browser profile handles login across runs; human-like scrolling for JS-rendered content

All scrapers share the same interface: async generators yielding raw listing dicts with keys `title`, `url`, `price`, `location`, `image_url`, `description`.

## Key Conventions

- All scrapers are async generators — yield dicts, never save directly
- Currency is always ZAR; `price` field is numeric or `None`
- `None` price causes scorer to return score 0, and `save_listing()` skips the listing
- Rate-limiting is per-scraper (2.0–2.5s sleep); no proxy support
- Logging uses loguru with daily rotation (7-day retention), configured in `main.py`
- No test suite exists; manual verification via `--report` flag
