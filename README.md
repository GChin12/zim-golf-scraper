# Zim Golf Deal Scraper

Scrapes South African used golf equipment platforms, scores every listing, and surfaces hot deals ready to post to your Zimbabwe WhatsApp channel.

---

## Platforms covered

| Platform | Method | Notes |
|---|---|---|
| Gumtree SA | httpx + BS4 | Static HTML, reliable |
| Bid or Buy | httpx + BS4 | Static HTML, has auctions + fixed price |
| OLX SA | httpx + BS4 | Static HTML |
| Facebook Marketplace | Playwright (headless) | Requires one-time manual login |

---

## Quick start

### 1. Install dependencies

```bash
# Python 3.11+ required
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env if needed (defaults work for local SQLite)
```

### 3. (Facebook only) Log in once

```bash
python scrapers/facebook.py --login
# A browser window opens — log in to Facebook, then press ENTER in terminal
```

### 4. Run the scraper

```bash
# Run all platforms once
python main.py

# Run a single platform
python main.py --platform gumtree
python main.py --platform bidorbuy
python main.py --platform olx

# Run on auto-schedule (every 5 hours)
python main.py --scheduler

# View top deals in DB
python main.py --report
```

---

## Deal scoring

Every listing is scored 0–100:

| Component | Points |
|---|---|
| Brand tier A (Callaway, TaylorMade, Titleist, Ping, Cobra) | +30 |
| Brand tier B (Cleveland, Mizuno, Wilson, Srixon) | +15 |
| Condition (brand new → worn) | +2 to +40 |
| Price vs category range (cheap → expensive) | +5 to +25 |
| High Zim demand item (irons, bags, polo shirts) | +20 |

**Score bands:**
- ≥ 65 → 🔥 Hot deal — auto-queue for WhatsApp post
- 50–64 → Manual review queue
- < 50 → Ignored

---

## Project structure

```
zim_golf_scraper/
├── main.py              # Orchestrator — run this
├── database.py          # SQLAlchemy models (Listing table)
├── scorer.py            # Deal scoring engine
├── requirements.txt
├── .env.example
├── scrapers/
│   ├── gumtree.py       # Gumtree SA
│   ├── bidorbuy.py      # Bid or Buy SA
│   ├── olx.py           # OLX SA
│   └── facebook.py      # Facebook Marketplace (Playwright)
├── data/
│   ├── golf_deals.db    # SQLite database (auto-created)
│   └── fb_profile/      # Facebook browser profile (auto-created)
└── logs/                # Daily log files (auto-created)
```

---

## Deploying to a VPS (to run 24/7)

```bash
# On a $6/mo DigitalOcean or Hetzner VPS:
git clone <your-repo>
cd zim_golf_scraper
pip install -r requirements.txt
playwright install chromium

# Run as a background service with screen or tmux:
screen -S golf_scraper
python main.py --scheduler
# Ctrl+A, D to detach

# Or use systemd (recommended for production):
# Copy zim_golf_scraper.service to /etc/systemd/system/
# systemctl enable zim_golf_scraper
# systemctl start zim_golf_scraper
```

---

## Next steps (Phase 2)

- `whatsapp_poster.py` — auto-post hot deals to your WA channel via Meta Cloud API
- `ai_copywriter.py` — Claude API rewrites listing text into punchy buyer-friendly posts
- Simple web dashboard to review + approve deals before posting
