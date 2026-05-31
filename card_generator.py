"""Kanda branded deal card generator — produces 1080×1080 PNG images."""

import io
import textwrap
import httpx
from PIL import Image, ImageDraw, ImageFont

# ── Brand colours (RGB tuples) ────────────────────────────────────────────────
C_GREEN = (29,  158, 117)   # #1D9E75  primary
C_DARK  = (15,  110,  86)   # #0F6E56  dark anchor / card bg
C_DEEP  = (8,    80,  65)   # #085041  deep background
C_MINT  = (93,  202, 165)   # #5DCAA5  accent
C_TAG   = (159, 225, 203)   # #9FE1CB  tagline
C_PALE  = (225, 245, 238)   # #E1F5EE  stroke on K
C_WHITE = (255, 255, 255)

CARD_W = CARD_H = 1080

# Font paths — Windows first, Linux fallbacks (fonts-liberation apt package)
_FONT_PATHS = {
    'sb': [
        r'C:\Windows\Fonts\georgiab.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf',
    ],
    's':  [
        r'C:\Windows\Fonts\georgia.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf',
    ],
    'si': [
        r'C:\Windows\Fonts\georgiai.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSerif-BoldItalic.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf',
    ],
    'a':  [
        r'C:\Windows\Fonts\arial.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ],
    'ab': [
        r'C:\Windows\Fonts\arialbd.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    ],
}


def _font(key: str, size: int) -> ImageFont.FreeTypeFont:
    for k in (key, 'ab', 'a', 'sb', 's'):
        for path in _FONT_PATHS.get(k, []):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _k_icon(size: int, alpha: int = 255) -> Image.Image:
    """Render the Kanda K icon mark as an RGBA image at the given pixel size."""
    img  = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s    = size / 400

    def p(x, y):
        return (int(x * s), int(y * s))

    lw = max(2, int(40 * s))
    r1, r2 = int(18 * s), int(14 * s)

    draw.rounded_rectangle([p(0, 0),   p(400, 400)], radius=int(80*s), fill=(*C_GREEN, alpha))
    draw.rounded_rectangle([p(20, 20), p(380, 380)], radius=int(60*s), fill=(*C_DEEP,  alpha))
    draw.line([p(110, 90),  p(110, 310)], fill=(*C_PALE, alpha), width=lw)
    draw.line([p(110, 200), p(290,  90)], fill=(*C_PALE, alpha), width=lw)
    draw.line([p(110, 200), p(290, 310)], fill=(*C_PALE, alpha), width=lw)
    draw.ellipse([p(60-r1, 60-r1), p(60+r1, 60+r1)], fill=(*C_MINT, int(alpha * 0.7)))
    draw.ellipse([p(340-r2, 340-r2), p(340+r2, 340+r2)], fill=(*C_MINT, int(alpha * 0.5)))
    return img


def _fetch_image(url: str | None, w: int, h: int) -> Image.Image:
    """Fetch and cover-crop a product image; return a branded placeholder on failure."""
    placeholder = Image.new('RGBA', (w, h), (*C_DARK, 255))
    k = _k_icon(min(w, h) // 2, alpha=55)
    placeholder.alpha_composite(k, ((w - k.width) // 2, (h - k.height) // 2))

    if not url:
        return placeholder
    try:
        r   = httpx.get(url, timeout=8, follow_redirects=True,
                        headers={'User-Agent': 'Mozilla/5.0'})
        img = Image.open(io.BytesIO(r.content)).convert('RGBA')
        tr  = w / h
        sr  = img.width / img.height
        if sr > tr:
            nw  = int(img.height * tr)
            img = img.crop(((img.width - nw) // 2, 0, (img.width + nw) // 2, img.height))
        else:
            nh  = int(img.width / tr)
            img = img.crop((0, (img.height - nh) // 2, img.width, (img.height + nh) // 2))
        return img.resize((w, h), Image.LANCZOS)
    except Exception:
        return placeholder


def _gradient(w: int, h: int, color: tuple) -> Image.Image:
    """Vertical gradient from transparent (top) to opaque color (bottom)."""
    img  = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i in range(h):
        a = int(255 * (i / h) ** 1.3)
        draw.line([(0, h - 1 - i), (w, h - 1 - i)], fill=(*color, a))
    return img


def _clean_condition(condition: str | None) -> str | None:
    if not condition:
        return None
    return condition.replace('_', ' ').replace('-', ' ').title()


_COND_COLOURS = {
    'Brand New': (29, 158, 117),
    'New':       (29, 158, 117),
    'Like New':  (40, 180, 130),
    'Excellent': (60, 160, 100),
    'Very Good': (80, 150, 90),
    'Good':      (110, 140, 80),
}


def generate_card(
    title:       str | None,
    brand:       str | None,
    condition:   str | None,
    sell_usd:    float | None,
    year:        str,
    description: str | None,
    hook:        str,
    image_urls:  list[str] | None = None,
) -> bytes:
    """Generate a 1080×1080 Kanda deal card. Accepts up to 4 product images."""

    urls     = [u for u in (image_urls or []) if u]
    n_imgs   = min(len(urls), 4)

    HEADER_H = 80
    GAP      = 4     # px gap between thumbnail images
    PAD      = 48

    # Image area dimensions depend on how many images we have
    if n_imgs <= 1:
        IMG_H     = 420
        STRIP_H   = 0
    else:
        IMG_H     = 305   # primary image
        STRIP_H   = 115   # thumbnail strip

    TOTAL_IMG_H = IMG_H + STRIP_H
    CONTENT_Y   = HEADER_H + TOTAL_IMG_H

    card = Image.new('RGBA', (CARD_W, CARD_H), (*C_DARK, 255))

    # ── Header bar ────────────────────────────────────────────────
    hdr   = Image.new('RGBA', (CARD_W, HEADER_H), (*C_DEEP, 255))
    hdraw = ImageDraw.Draw(hdr)
    k_h   = _k_icon(52)
    hdr.alpha_composite(k_h, (22, 14))
    lbl = "KANDA SPORTS DEALS"
    lf  = _font('ab', 21)
    bb  = hdraw.textbbox((0, 0), lbl, font=lf)
    hdraw.text(((CARD_W - (bb[2]-bb[0])) // 2, (HEADER_H - (bb[3]-bb[1])) // 2),
               lbl, font=lf, fill=(*C_MINT, 255))
    card.alpha_composite(hdr, (0, 0))

    # ── Primary product image ─────────────────────────────────────
    primary_url = urls[0] if urls else None
    primary     = _fetch_image(primary_url, CARD_W, IMG_H)
    card.alpha_composite(primary, (0, HEADER_H))

    # ── Thumbnail strip (if multiple images) ──────────────────────
    if n_imgs >= 2:
        thumbs  = urls[1:n_imgs]   # 1–3 extra images
        n_t     = len(thumbs)
        thumb_w = (CARD_W - (n_t - 1) * GAP) // n_t
        for i, url in enumerate(thumbs):
            x = i * (thumb_w + GAP)
            t = _fetch_image(url, thumb_w, STRIP_H)
            card.alpha_composite(t, (x, HEADER_H + IMG_H))
        # thin separator between primary and strip
        sep = Image.new('RGBA', (CARD_W, GAP), (*C_DEEP, 255))
        card.alpha_composite(sep, (0, HEADER_H + IMG_H - GAP // 2))

    # ── Gradient fade into content area ───────────────────────────
    fade_h = 130
    card.alpha_composite(_gradient(CARD_W, fade_h, C_DARK),
                         (0, CONTENT_Y - fade_h))

    # ── Solid content background ──────────────────────────────────
    draw = ImageDraw.Draw(card)
    draw.rectangle([0, CONTENT_Y, CARD_W, CARD_H], fill=(*C_DARK, 255))

    # ── Condition badge overlaid on image (bottom-left) ───────────
    cond_clean = _clean_condition(condition)
    if cond_clean:
        cf      = _font('ab', 26)
        bb      = draw.textbbox((0, 0), cond_clean.upper(), font=cf)
        tw, th  = bb[2] - bb[0], bb[3] - bb[1]
        bpad    = 10
        rx, ry  = PAD, CONTENT_Y - th - bpad * 2 - 6
        col     = _COND_COLOURS.get(cond_clean, (120, 140, 130))
        draw.rounded_rectangle(
            [rx - bpad, ry - bpad // 2, rx + tw + bpad, ry + th + bpad // 2],
            radius=8, fill=(*col, 230))
        draw.text((rx, ry), cond_clean.upper(), font=cf, fill=(*C_WHITE, 255))

    # ── Content area ──────────────────────────────────────────────
    y = CONTENT_Y + 18

    # Brand
    brand_str = brand.title() if brand and brand != 'Unknown' else None
    if brand_str:
        bf = _font('ab', 22)
        draw.text((PAD, y), brand_str.upper(), font=bf, fill=(*C_MINT, 200))
        y += draw.textbbox((0, 0), brand_str.upper(), font=bf)[3] + 6

    # Title — strip leading brand if already shown
    title_str = (title or '').strip()
    if brand_str and title_str.lower().startswith(brand_str.lower()):
        title_str = title_str[len(brand_str):].strip(' -—')
    tf = _font('sb', 46)
    for line in textwrap.wrap(title_str, width=28)[:2]:
        draw.text((PAD, y), line, font=tf, fill=(*C_WHITE, 255))
        y += draw.textbbox((0, 0), line, font=tf)[3] + 4
    y += 14

    # Description snippet
    if description:
        snippet = ' '.join(l.strip() for l in description.strip().splitlines() if l.strip())[:220]
        df = _font('a', 24)
        for line in textwrap.wrap(snippet, width=52)[:2]:
            draw.text((PAD, y), line, font=df, fill=(*C_PALE, 160))
            y += draw.textbbox((0, 0), line, font=df)[3] + 2
        y += 12

    # Year + price — big and prominent
    if sell_usd or year:
        pf       = _font('sb', 62)
        price_s  = f"${sell_usd:,.0f}" if sell_usd else ""
        year_s   = year or ""
        info_str = f"{year_s}   {price_s}".strip() if (year_s and price_s) else (year_s or price_s)
        draw.text((PAD, y), info_str, font=pf, fill=(*C_WHITE, 255))
        y += draw.textbbox((0, 0), info_str, font=pf)[3] + 16

    # Hook
    if hook:
        hf = _font('si', 28)
        for line in textwrap.wrap(hook, width=50)[:2]:
            draw.text((PAD, y), line, font=hf, fill=(*C_MINT, 215))
            y += draw.textbbox((0, 0), line, font=hf)[3] + 3

    # ── Footer ────────────────────────────────────────────────────
    tgf = _font('si', 24)
    draw.text((PAD, CARD_H - 46), 'wa.me/27786093328  ·  Up your game.', font=tgf, fill=(*C_TAG, 255))

    # K watermark (bottom-right, low opacity)
    k_wm = _k_icon(118, alpha=40)
    card.alpha_composite(k_wm, (CARD_W - 136, CARD_H - 136))

    buf = io.BytesIO()
    card.convert('RGB').save(buf, 'PNG', optimize=True)
    return buf.getvalue()
