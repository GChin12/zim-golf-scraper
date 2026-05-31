"""
Web dashboard for the Zim Golf Deal Scraper.

Usage:
  python web.py
  Then open http://localhost:5000
"""

import asyncio
import io
import os
import re
import random
import threading
import zipfile
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse, urljoin
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file
from sqlalchemy import or_
from sqlalchemy.orm import Session
from apscheduler.schedulers.background import BackgroundScheduler
from database import init_db, Listing, PricingConfig
from card_generator import generate_card
from scorer import score_listing
from gumtree import scrape_gumtree
from bidorbuy import scrape_bidorbuy
from olx import scrape_olx

DB_URL = os.getenv("DATABASE_URL", "sqlite:///data/golf_deals.db")
_, SessionFactory = init_db(DB_URL)

app = Flask(__name__)

# ── Scrape status (in-memory, resets on restart) ──────────────────────────────
SCRAPE_STATUS = {
    "running":   False,
    "last_run":  None,   # datetime
    "last_new":  0,
    "last_hot":  0,
    "last_secs": 0,
    "next_run":  None,   # datetime
}
SCRAPE_INTERVAL_HOURS = 5


def save_listing(raw: dict, db: Session) -> Listing | None:
    url_hash = Listing.make_hash(raw["url"])
    if db.query(Listing).filter_by(url_hash=url_hash).first():
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


async def _run_all_async():
    db = SessionFactory()
    total_new = total_hot = 0
    try:
        for scraper, kwargs in [
            (scrape_gumtree,  {"max_pages": 3, "delay": 2.5}),
            (scrape_bidorbuy, {"max_pages": 3, "delay": 2.0}),
            (scrape_olx,      {"max_pages": 3, "delay": 2.0}),
        ]:
            try:
                async for raw in scraper(**kwargs):
                    listing = save_listing(raw, db)
                    if listing:
                        total_new += 1
                        if listing.is_hot_deal:
                            total_hot += 1
            except Exception as e:
                app.logger.error(f"Scraper error: {e}")
    finally:
        db.close()
    return total_new, total_hot


def run_scrape_job():
    if SCRAPE_STATUS["running"]:
        return
    SCRAPE_STATUS["running"] = True
    start = datetime.utcnow()
    try:
        new, hot = asyncio.run(_run_all_async())
        elapsed = (datetime.utcnow() - start).seconds
        SCRAPE_STATUS.update(last_run=datetime.utcnow(), last_new=new,
                             last_hot=hot, last_secs=elapsed)
    except Exception as e:
        app.logger.error(f"Scrape job failed: {e}")
    finally:
        SCRAPE_STATUS["running"] = False
        job = scheduler.get_job("scrape")
        if job:
            SCRAPE_STATUS["next_run"] = job.next_run_time.replace(tzinfo=None)


# Start background scheduler (guard against Werkzeug double-load in debug mode)
scheduler = BackgroundScheduler()
scheduler.add_job(run_scrape_job, "interval", hours=SCRAPE_INTERVAL_HOURS,
                  id="scrape", next_run_time=datetime.utcnow())  # run immediately on start
if not os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    scheduler.start()
    job = scheduler.get_job("scrape")
    if job:
        SCRAPE_STATUS["next_run"] = job.next_run_time.replace(tzinfo=None)


PLATFORMS   = ["gumtree", "bidorbuy", "olx"]
CATEGORIES  = ["clubs", "bags", "balls", "attire", "shoes", "other"]
CONDITIONS  = ["brand new", "new", "like new", "excellent", "very good", "good", "used", "fair", "worn"]
PAGE_SIZE   = 50
RATE_STALE_DAYS = 7

GAUTENG_TERMS = [
    "gauteng", "johannesburg", "pretoria", "sandton", "centurion",
    "midrand", "randburg", "roodepoort", "soweto", "tshwane",
    "ekurhuleni", "benoni", "boksburg", "germiston", "kempton",
    "alberton", "edenvale", "fourways", "bedfordview",
]

CAT_EMOJI = {"clubs": "⛳", "bags": "🎒", "balls": "🏌️", "attire": "👕", "shoes": "👟", "other": "🏪"}

HOOKS = {
    "clubs": [
        "Upgrade your game without breaking the bank!",
        "This set won't last long — grab it before it's gone!",
        "Perfect for the serious golfer on a budget.",
        "Hit further, play better — at a fraction of retail price.",
    ],
    "bags":   ["Carry your kit in style!", "The perfect companion for your round.", "Great bag at an unbeatable price."],
    "balls":  ["Stock up and save big!", "Premium balls at a fraction of shop price.", "Never run short on the course again!"],
    "attire": ["Look the part without paying full price!", "Premium golf fashion — serious discount.", "Dress sharp on the fairway!"],
    "shoes":  ["Comfort and grip at the right price.", "Walk every hole in style!", "Premium footwear — massive saving."],
    "other":  ["Great golf find — don't miss out!", "Snag this deal before someone else does!"],
}


# ── Pricing ───────────────────────────────────────────────────────────────────

def get_config(db: Session) -> PricingConfig:
    cfg = db.get(PricingConfig, 1)
    if not cfg:
        cfg = PricingConfig(id=1)
        db.add(cfg)
        db.commit()
    return cfg


def calculate_price(listing: Listing, cfg: PricingConfig) -> dict | None:
    if not listing.price_zar or listing.price_zar <= 0:
        return None
    cat = listing.category or "other"
    ship_map = {
        "clubs": cfg.ship_clubs, "bags": cfg.ship_bags, "balls": cfg.ship_balls,
        "attire": cfg.ship_attire, "shoes": cfg.ship_shoes, "other": cfg.ship_other,
    }
    sourcing = listing.price_zar / cfg.usd_rate
    shipping = ship_map.get(cat, cfg.ship_other)
    other    = cfg.other_costs
    landed   = sourcing + shipping + other
    markup   = landed * (cfg.markup_pct / 100)
    sell     = landed + markup
    return {
        "sourcing_usd": round(sourcing, 2),
        "shipping_usd": round(shipping, 2),
        "other_usd":    round(other,    2),
        "landed_usd":   round(landed,   2),
        "markup_usd":   round(markup,   2),
        "markup_pct":   cfg.markup_pct,
        "sell_usd":     round(sell,     2),
        "usd_rate":     cfg.usd_rate,
    }


# ── WA message ────────────────────────────────────────────────────────────────

YEAR_RE = re.compile(r'\b(19[7-9]\d|20[0-2]\d)\b')


def search_manufacture_year(title: str, brand: str | None, category: str | None) -> str:
    """Search DuckDuckGo for the manufacture year of a golf product. Returns '' if not found."""
    import httpx
    # Build a tight search query
    parts = [p for p in [brand, title, "golf", category, "year made released"] if p and p != "Unknown"]
    query = " ".join(parts[:6])
    try:
        r = httpx.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = r.json()
        for field in ("AbstractText", "Answer", "Definition", "AbstractSource"):
            text = data.get(field) or ""
            m = YEAR_RE.search(text)
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""


def generate_wa_message(listing: Listing, sell_usd: float | None = None, year: str = "") -> str:
    cat   = listing.category or "other"
    emoji = CAT_EMOJI.get(cat, "⛳")
    hook  = random.choice(HOOKS.get(cat, HOOKS["other"]))
    title = (listing.title or "").upper()
    brand = listing.brand if listing.brand and listing.brand != "Unknown" else None
    hot   = "🔥 *HOT DEAL* 🔥\n\n" if listing.is_hot_deal else ""

    condition = (listing.condition or "").title()
    title_part = f"{brand.title()} {listing.title or ''}" if brand else (listing.title or "")
    header = f"*{title_part.strip()}*"
    if condition:
        header += f" — {condition}"

    lines = [f"{hot}{header}", ""]

    if listing.description:
        desc_lines = [l.strip() for l in listing.description.strip().splitlines() if l.strip()][:2]
        lines.append(" ".join(desc_lines)[:280])
        lines.append("")

    info = []
    if year:     info.append(f"\U0001f4c5 {year}")
    if sell_usd: info.append(f"\U0001f4b5 ${sell_usd:,.0f}")
    if info:
        lines += ["  ·  ".join(info), ""]

    lines += [hook, "", "_Kanda Sports Deals · Up your game._", "#KandaSports #ZimGolf"]
    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

@app.context_processor
def inject_helpers():
    def page_url(p):
        args = request.args.to_dict(flat=True)
        args["page"] = p
        return "?" + urlencode(args)
    return dict(page_url=page_url)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    db: Session = SessionFactory()
    try:
        q = db.query(Listing).filter(Listing.active == True)

        platform  = request.args.get("platform", "")
        category  = request.args.get("category", "")
        condition = request.args.get("condition", "")
        hot_only  = request.args.get("hot_only", "")
        min_score = request.args.get("min_score", "", type=str)
        max_price = request.args.get("max_price", "", type=str)
        gauteng   = request.args.get("gauteng", "1")
        golf_only = request.args.get("golf_only", "1")
        page      = request.args.get("page", 1, type=int)

        if platform:  q = q.filter(Listing.platform == platform)
        if category:  q = q.filter(Listing.category == category)
        if condition: q = q.filter(Listing.condition == condition)
        if hot_only:  q = q.filter(Listing.is_hot_deal == True)
        if min_score: q = q.filter(Listing.deal_score >= int(min_score))
        if max_price: q = q.filter(Listing.price_zar <= float(max_price))
        if gauteng:   q = q.filter(or_(*[Listing.location.ilike(f"%{t}%") for t in GAUTENG_TERMS]))
        if golf_only: q = q.filter(or_(Listing.category != "other", Listing.brand != "Unknown"))

        total    = q.count()
        pages    = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page     = max(1, min(page, pages))
        listings = q.order_by(Listing.deal_score.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()

        cfg          = get_config(db)
        stale_cutoff = datetime.utcnow() - timedelta(days=RATE_STALE_DAYS)
        rate_stale   = cfg.updated_at is None or cfg.updated_at < stale_cutoff
        rate_age_days = (datetime.utcnow() - cfg.updated_at).days if cfg.updated_at else None

        sell_prices = {}
        for l in listings:
            p = calculate_price(l, cfg)
            if p:
                sell_prices[l.id] = p["sell_usd"]

        stats = {
            "total":    db.query(Listing).filter(Listing.active == True).count(),
            "hot":      db.query(Listing).filter(Listing.is_hot_deal == True, Listing.active == True).count(),
            "posted":   db.query(Listing).filter(Listing.posted_to_wa == True, Listing.active == True).count(),
            "platforms": {p: db.query(Listing).filter(Listing.platform == p, Listing.active == True).count()
                          for p in PLATFORMS},
        }

        return render_template(
            "index.html",
            listings=listings,
            stats=stats,
            total=total,
            page=page,
            pages=pages,
            platforms=PLATFORMS,
            categories=CATEGORIES,
            conditions=CONDITIONS,
            sell_prices=sell_prices,
            rate_stale=rate_stale,
            rate_age_days=rate_age_days,
            usd_rate=cfg.usd_rate,
            filters={
                "platform": platform, "category": category, "condition": condition,
                "hot_only": hot_only, "min_score": min_score, "max_price": max_price,
                "gauteng": gauteng, "golf_only": golf_only,
            },
        )
    finally:
        db.close()


@app.route("/settings", methods=["GET", "POST"])
def settings():
    db: Session = SessionFactory()
    try:
        cfg = get_config(db)
        if request.method == "POST":
            cfg.usd_rate    = float(request.form["usd_rate"])
            cfg.markup_pct  = float(request.form["markup_pct"])
            cfg.ship_clubs  = float(request.form["ship_clubs"])
            cfg.ship_bags   = float(request.form["ship_bags"])
            cfg.ship_balls  = float(request.form["ship_balls"])
            cfg.ship_attire = float(request.form["ship_attire"])
            cfg.ship_shoes  = float(request.form["ship_shoes"])
            cfg.ship_other  = float(request.form["ship_other"])
            cfg.other_costs = float(request.form["other_costs"])
            cfg.updated_at  = datetime.utcnow()
            db.commit()
            return redirect(url_for("settings"))

        stale_cutoff = datetime.utcnow() - timedelta(days=RATE_STALE_DAYS)
        rate_stale   = cfg.updated_at is None or cfg.updated_at < stale_cutoff
        return render_template("settings.html", cfg=cfg, rate_stale=rate_stale,
                               stale_days=RATE_STALE_DAYS)
    finally:
        db.close()


@app.route("/api/listing/<int:listing_id>")
def api_listing(listing_id):
    db: Session = SessionFactory()
    try:
        listing = db.get(Listing, listing_id)
        if not listing:
            return jsonify({"error": "not found"}), 404
        cfg     = get_config(db)
        pricing = calculate_price(listing, cfg)
        sell    = pricing["sell_usd"] if pricing else None
        return jsonify({
            "id":           listing.id,
            "title":        listing.title,
            "price_zar":    listing.price_zar,
            "brand":        listing.brand,
            "category":     listing.category,
            "condition":    listing.condition,
            "location":     listing.location,
            "image_url":    listing.image_url,
            "url":          listing.url,
            "platform":     listing.platform,
            "deal_score":   listing.deal_score,
            "is_hot_deal":  listing.is_hot_deal,
            "posted_to_wa": listing.posted_to_wa,
            "wa_message":   generate_wa_message(listing, sell),
            "pricing":      pricing,
        })
    finally:
        db.close()


@app.route("/api/image/<int:listing_id>")
def proxy_image(listing_id):
    import httpx
    from flask import Response
    db: Session = SessionFactory()
    try:
        listing = db.get(Listing, listing_id)
        if not listing or not listing.image_url:
            return "", 404
        r = httpx.get(listing.image_url, timeout=10, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"})
        content_type = r.headers.get("content-type", "image/jpeg").split(";")[0]
        return Response(r.content, content_type=content_type)
    except Exception:
        return "", 502
    finally:
        db.close()


@app.route("/api/scrape", methods=["POST"])
def trigger_scrape():
    if SCRAPE_STATUS["running"]:
        return jsonify({"ok": False, "msg": "Already running"})
    threading.Thread(target=run_scrape_job, daemon=True).start()
    return jsonify({"ok": True, "msg": "Scrape started"})


@app.route("/api/scrape/status")
def scrape_status():
    def fmt(dt):
        if not dt:
            return None
        diff = (dt - datetime.utcnow()).total_seconds()
        if diff < 0:
            return "now"
        h, rem = divmod(int(diff), 3600)
        m = rem // 60
        return f"{h}h {m}m" if h else f"{m}m"

    return jsonify({
        "running":   SCRAPE_STATUS["running"],
        "last_run":  SCRAPE_STATUS["last_run"].strftime("%d %b %H:%M") if SCRAPE_STATUS["last_run"] else None,
        "last_new":  SCRAPE_STATUS["last_new"],
        "last_hot":  SCRAPE_STATUS["last_hot"],
        "next_in":   fmt(SCRAPE_STATUS["next_run"]),
    })


@app.route("/posted")
def posted():
    db: Session = SessionFactory()
    try:
        listings = (
            db.query(Listing)
            .filter(Listing.posted_to_wa == True, Listing.active == True)
            .order_by(Listing.wa_posted_at.desc())
            .all()
        )
        cfg = get_config(db)
        sell_prices = {l.id: p["sell_usd"] for l in listings if (p := calculate_price(l, cfg))}
        return render_template("posted.html", listings=listings, sell_prices=sell_prices)
    finally:
        db.close()


@app.route("/api/card/<int:listing_id>")
def deal_card(listing_id):
    year         = request.args.get("year", "")
    sell_override = request.args.get("sell_usd", None, type=float)
    db: Session  = SessionFactory()
    try:
        listing = db.get(Listing, listing_id)
        if not listing:
            return "", 404
        cfg     = get_config(db)
        pricing = calculate_price(listing, cfg)
        sell    = sell_override if sell_override is not None else (pricing["sell_usd"] if pricing else None)
        cat     = listing.category or "other"
        hook    = random.choice(HOOKS.get(cat, HOOKS["other"]))
        png     = generate_card(
            title       = listing.title,
            brand       = listing.brand,
            condition   = listing.condition,
            sell_usd    = sell,
            year        = year,
            description = listing.description,
            hook        = hook,
            image_url   = listing.image_url,
        )
        safe = re.sub(r'[^\w-]', '_', (listing.title or 'deal')[:30]).strip('_')
        from flask import Response
        return Response(png, mimetype='image/png',
                        headers={"Content-Disposition": f'attachment; filename="{safe}.png"'})
    finally:
        db.close()


@app.route("/api/year/<int:listing_id>")
def lookup_year(listing_id):
    db: Session = SessionFactory()
    try:
        listing = db.get(Listing, listing_id)
        if not listing:
            return jsonify({"year": ""}), 404
        year = search_manufacture_year(listing.title or "", listing.brand, listing.category)
        return jsonify({"year": year})
    finally:
        db.close()


@app.route("/api/mark-posted/<int:listing_id>", methods=["POST"])
def mark_posted(listing_id):
    db: Session = SessionFactory()
    try:
        listing = db.get(Listing, listing_id)
        if not listing:
            return jsonify({"error": "not found"}), 404
        listing.posted_to_wa = True
        listing.wa_posted_at = datetime.utcnow()
        db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()


def extract_listing_images(listing_url: str, fallback_url: str | None) -> list[str]:
    """Fetch the listing page and return all product image URLs found."""
    import httpx
    from bs4 import BeautifulSoup

    EXCLUDE = re.compile(r'logo|icon|avatar|spinner|placeholder|banner|pixel|1x1|tracking|svg', re.I)
    IMG_EXT = re.compile(r'\.(jpe?g|png|webp)(\?|$)', re.I)

    def normalise(src: str, base: str) -> str | None:
        if not src:
            return None
        src = src.strip()
        if src.startswith('//'):
            src = 'https:' + src
        elif src.startswith('/'):
            parsed = urlparse(base)
            src = f"{parsed.scheme}://{parsed.netloc}{src}"
        elif not src.startswith('http'):
            return None
        return src

    try:
        r = httpx.get(listing_url, timeout=15, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        soup = BeautifulSoup(r.text, 'html.parser')
    except Exception:
        return [fallback_url] if fallback_url else []

    seen_keys: set[str] = set()
    images: list[str] = []

    def add(src: str | None):
        url = normalise(src, listing_url)
        if not url:
            return
        key = url.split('?')[0]  # dedupe ignoring query params
        if key in seen_keys or EXCLUDE.search(key) or not IMG_EXT.search(key):
            return
        seen_keys.add(key)
        images.append(url)

    # Collect from img tags (multiple attrs scrapers use)
    for img in soup.find_all('img'):
        for attr in ('src', 'data-src', 'data-lazy-src', 'data-original', 'data-zoom-image'):
            add(img.get(attr))

    # Collect from anchor hrefs pointing directly at images
    for a in soup.find_all('a', href=True):
        add(a['href'])

    # BobShop embeds product JSON in <script> tags
    for script in soup.find_all('script', type='application/json'):
        for match in re.findall(r'https?://[^\s"\']+\.(?:jpe?g|png|webp)', script.string or ''):
            add(match)

    # Ensure the stored thumbnail is always included
    if fallback_url:
        add(fallback_url)

    return images[:20]


@app.route("/api/images/<int:listing_id>")
def download_all_images(listing_id):
    import httpx
    db: Session = SessionFactory()
    try:
        listing = db.get(Listing, listing_id)
        if not listing:
            return "", 404

        urls = extract_listing_images(listing.url, listing.image_url)
        if not urls:
            return jsonify({"error": "no images found"}), 404

        buf = io.BytesIO()
        downloaded = 0
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for i, url in enumerate(urls, 1):
                try:
                    r = httpx.get(url, timeout=10, follow_redirects=True,
                                  headers={"User-Agent": "Mozilla/5.0"})
                    if r.status_code == 200 and r.content:
                        ext = url.split('?')[0].rsplit('.', 1)[-1].lower() or 'jpg'
                        zf.writestr(f"image_{i:02d}.{ext}", r.content)
                        downloaded += 1
                except Exception:
                    continue

        if downloaded == 0:
            return jsonify({"error": "could not download any images"}), 502

        buf.seek(0)
        safe = re.sub(r'[^\w-]', '_', (listing.title or 'listing')[:30]).strip('_')
        return send_file(buf, mimetype='application/zip', as_attachment=True,
                         download_name=f"{safe}.zip")
    finally:
        db.close()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
