"""
Deal scoring engine — grades every listing 0–100 based on brand,
condition, price-to-market ratio, and Zim demand signals.

Score bands:
  >= 65  → auto-queue for WhatsApp post
  50–64  → manual review queue
  < 50   → ignored
"""

import re
from dataclasses import dataclass, field
from typing import Optional

# ── Brand tiers ──────────────────────────────────────────────────────────────
BRAND_TIER_A = {
    "callaway", "taylormade", "taylor made", "titleist", "ping", "cobra",
    "footjoy", "foot joy",
}
BRAND_TIER_B = {
    "cleveland", "mizuno", "wilson", "srixon", "bridgestone",
    "nike golf", "adidas golf", "under armour golf", "puma golf",
}
BRAND_TIER_C = {
    "spalding", "dunlop", "slazenger", "hippo", "ram",
}

# ── Condition keywords ────────────────────────────────────────────────────────
CONDITION_MAP = {
    "brand new": 40, "new": 38,
    "like new": 35, "as new": 35, "excellent": 30,
    "good": 22, "very good": 28,
    "fair": 12, "used": 15,
    "worn": 8, "damaged": 2, "spares": 0, "parts": 0,
}

# ── Categories ────────────────────────────────────────────────────────────────
CATEGORY_KEYWORDS = {
    "clubs":   ["iron", "irons", "driver", "wedge", "putter", "wood", "hybrid",
                "club", "clubs", "set", "shaft"],
    "bags":    ["bag", "trolley", "cart", "carry bag", "stand bag", "staff bag"],
    "balls":   ["ball", "balls", "dozen", "sleeve"],
    "attire":  ["shirt", "polo", "pants", "trousers", "shorts", "jacket",
                "glove", "gloves", "cap", "hat", "vest", "sweater"],
    "shoes":   ["shoe", "shoes", "boot", "boots", "footwear", "cleats"],
}

# ── High-demand items for the Zim market ─────────────────────────────────────
ZIM_HOT_ITEMS = [
    "callaway irons", "taylormade irons", "titleist irons", "ping irons",
    "golf bag", "stand bag", "trolley",
    "polo shirt", "golf shirt",
    "callaway driver", "taylormade driver",
    "full set", "complete set",
    "footjoy shoes", "golf shoes",
]

# ── Price sanity ranges by category (ZAR) ────────────────────────────────────
PRICE_RANGES = {
    "clubs":  (200,  15_000),
    "bags":   (150,   5_000),
    "balls":  (50,    2_000),
    "attire": (50,    2_500),
    "shoes":  (100,   3_500),
    "other":  (50,   20_000),
}


@dataclass
class ScoreBreakdown:
    brand_score:     int = 0
    condition_score: int = 0
    price_score:     int = 0
    demand_score:    int = 0
    total:           int = 0
    reasons:         list = field(default_factory=list)


def detect_brand(text: str) -> tuple[str, int]:
    """Return (brand_name, score) from listing text."""
    text_lower = text.lower()
    for brand in BRAND_TIER_A:
        if brand in text_lower:
            return brand.title(), 30
    for brand in BRAND_TIER_B:
        if brand in text_lower:
            return brand.title(), 15
    for brand in BRAND_TIER_C:
        if brand in text_lower:
            return brand.title(), 5
    return "Unknown", 0


def detect_condition(text: str) -> tuple[str, int]:
    """Return (condition_label, score)."""
    text_lower = text.lower()
    best_score = 0
    best_label = "used"
    for kw, score in CONDITION_MAP.items():
        if kw in text_lower and score > best_score:
            best_score = score
            best_label = kw
    return best_label.title(), best_score


def detect_category(text: str) -> str:
    """Return category string from title/description."""
    text_lower = text.lower()
    scores = {cat: 0 for cat in CATEGORY_KEYWORDS}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                scores[cat] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other"


def price_score(price_zar: Optional[float], category: str) -> tuple[int, str]:
    """
    Score based on price reasonableness.
    Cheap-but-not-suspiciously-cheap items score highest.
    """
    if not price_zar or price_zar <= 0:
        return 0, "no price"

    lo, hi = PRICE_RANGES.get(category, PRICE_RANGES["other"])
    if price_zar < lo:
        return 5, f"very low price R{price_zar:.0f} — possible scam"
    if price_zar > hi:
        return 0, f"price R{price_zar:.0f} exceeds max for {category}"

    # Score highest in the lower quarter of the range
    ratio = (price_zar - lo) / (hi - lo)   # 0 = cheapest, 1 = most expensive
    if ratio <= 0.25:
        return 25, f"great price R{price_zar:.0f}"
    if ratio <= 0.50:
        return 18, f"good price R{price_zar:.0f}"
    if ratio <= 0.75:
        return 10, f"mid price R{price_zar:.0f}"
    return 5, f"high price R{price_zar:.0f}"


def zim_demand_score(text: str) -> tuple[int, str]:
    """Bonus points for items historically in demand in Zimbabwe."""
    text_lower = text.lower()
    for phrase in ZIM_HOT_ITEMS:
        if phrase in text_lower:
            return 20, f"high Zim demand: '{phrase}'"
    return 0, ""


def score_listing(
    title: str,
    description: str = "",
    price_zar: Optional[float] = None,
) -> tuple[ScoreBreakdown, str, str, str]:
    """
    Score a listing and return (breakdown, brand, condition, category).
    Call this before saving to the DB.
    """
    full_text = f"{title} {description}"
    bd = ScoreBreakdown()

    brand, bd.brand_score = detect_brand(full_text)
    if bd.brand_score:
        bd.reasons.append(f"brand={brand} (+{bd.brand_score})")

    condition, bd.condition_score = detect_condition(full_text)
    bd.reasons.append(f"condition={condition} (+{bd.condition_score})")

    category = detect_category(full_text)

    ps, pr = price_score(price_zar, category)
    bd.price_score = ps
    if pr:
        bd.reasons.append(pr + f" (+{ps})")

    ds, dr = zim_demand_score(full_text)
    bd.demand_score = ds
    if dr:
        bd.reasons.append(dr + f" (+{ds})")

    bd.total = min(100, bd.brand_score + bd.condition_score + bd.price_score + bd.demand_score)
    return bd, brand, condition, category
