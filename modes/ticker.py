#!/usr/bin/env python3
"""
tinyscreen crypto ticker — scrolling cryptocurrency prices with sparklines.

Fetches from CoinGecko free API (no key required).
Designed for 1920x440 stretched bar LCDs.
"""

import time
from PIL import Image, ImageDraw, ImageFont
from modes.glow import paste_hero_text, glow_accent_line, glow_side_edge, glow_line

# ── Colors (vivid, saturated — matching sysmon dashboard) ──────────
BG          = (5, 7, 12)
PANEL_BG    = (10, 14, 24)
ACCENT      = (0, 210, 255)
TEXT        = (220, 225, 240)
TEXT_DIM    = (65, 75, 100)
TEXT_BRIGHT = (252, 254, 255)
GREEN       = (0, 255, 140)
RED         = (255, 50, 50)
YELLOW      = (255, 225, 0)

# ── Utility ────────────────────────────────────────────────────────
def _lerp_color(c1, c2, t):
    """Linearly interpolate between two RGB tuples."""
    t = max(0.0, min(1.0, t))
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

# ── Font cache ──────────────────────────────────────────────────────
_fonts = {}

def font(size):
    if size not in _fonts:
        for path in ['/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
                     '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                     '/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf',
                     '/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf']:
            try:
                _fonts[size] = ImageFont.truetype(path, size)
                return _fonts[size]
            except Exception:
                continue
        _fonts[size] = ImageFont.load_default()
    return _fonts[size]

# ── Cached background and scanlines ────────────────────────────────
_bg_cache = {}

def _get_bg(w, h):
    """Generate gradient background with grid, horizon glow, and vignette. Cached."""
    if (w, h) not in _bg_cache:
        img = Image.new('RGBA', (w, h), BG + (255,))
        draw = ImageDraw.Draw(img)
        # Vertical gradient
        for y in range(h):
            t = y / max(h - 1, 1)
            c = _lerp_color((8, 12, 22), (4, 6, 12), t)
            draw.line([(0, y), (w, y)], fill=c)
        # Grid
        grid_c = (18, 24, 38, 50)
        for gx in range(0, w, 40):
            draw.line([(gx, 0), (gx, h)], fill=grid_c)
        for gy in range(0, h, 40):
            draw.line([(0, gy), (w, gy)], fill=grid_c)
        # Grid dots at intersections
        for gx in range(0, w, 80):
            for gy in range(0, h, 80):
                draw.ellipse([gx - 2, gy - 2, gx + 2, gy + 2], fill=ACCENT + (25,))
                draw.ellipse([gx - 1, gy - 1, gx + 1, gy + 1], fill=ACCENT + (45,))
        # Bottom horizon glow
        for i in range(50):
            a = int(30 * (1.0 - i / 50))
            draw.line([(0, h - 1 - i), (w, h - 1 - i)], fill=(0, 100, 180, a))
        # Vignette
        vig = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        vd = ImageDraw.Draw(vig)
        for i in range(40):
            a = int(50 * (1.0 - i / 40))
            vd.line([(0, i), (w, i)], fill=(0, 0, 0, a))
            vd.line([(0, h - 1 - i), (w, h - 1 - i)], fill=(0, 0, 0, a))
        for i in range(60):
            a = int(40 * (1.0 - i / 60))
            vd.line([(i, 0), (i, h)], fill=(0, 0, 0, a))
            vd.line([(w - 1 - i, 0), (w - 1 - i, h)], fill=(0, 0, 0, a))
        img = Image.alpha_composite(img, vig)
        _bg_cache[(w, h)] = img
    return _bg_cache[(w, h)].copy()

_scanline_cache = {}

def _get_scanlines(w, h):
    """Semi-transparent horizontal scanlines for HUD effect. Cached."""
    key = (w, h)
    if key not in _scanline_cache:
        sl = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sl)
        for y in range(0, h, 3):
            sd.line([(0, y), (w, y)], fill=(0, 0, 0, 55))
        _scanline_cache[key] = sl
    return _scanline_cache[key]

# ── Coin configuration ──────────────────────────────────────────────
COINS = [
    ('bitcoin',      'BTC'),
    ('ethereum',     'ETH'),
    ('solana',       'SOL'),
    ('dogecoin',     'DOGE'),
    ('cardano',      'ADA'),
    ('polkadot',     'DOT'),
    ('chainlink',    'LINK'),
    ('avalanche-2',  'AVAX'),
]

API_URL = (
    'https://api.coingecko.com/api/v3/simple/price'
    '?ids=bitcoin,ethereum,solana,dogecoin,cardano,polkadot,chainlink,avalanche-2'
    '&vs_currencies=usd&include_24hr_change=true'
)

FETCH_INTERVAL = 60  # seconds

# ── State ───────────────────────────────────────────────────────────
_last_fetch = 0.0
_prices = {}          # {coin_id: {'usd': float, 'usd_24h_change': float}}
_price_history = {}   # {coin_id: [price, price, ...]}  up to 60 entries
_scroll_offset = 0.0
_last_render_time = 0.0
_fetch_error = None

def init():
    """Initialize / reset ticker state."""
    global _last_fetch, _prices, _price_history, _scroll_offset
    global _last_render_time, _fetch_error
    _last_fetch = 0.0
    _prices = {}
    _price_history = {cid: [] for cid, _ in COINS}
    _scroll_offset = 0.0
    _last_render_time = time.monotonic()
    _fetch_error = None
    _fetch_prices()

def _fetch_prices():
    """Fetch prices from CoinGecko. Updates module-level state."""
    global _last_fetch, _prices, _fetch_error
    try:
        import requests
        resp = requests.get(API_URL, timeout=10, headers={
            'Accept': 'application/json',
            'User-Agent': 'tinyscreen-ticker/1.0',
        })
        resp.raise_for_status()
        data = resp.json()
        _prices = data
        _fetch_error = None

        # Append to history
        for coin_id, _ in COINS:
            if coin_id in data and 'usd' in data[coin_id]:
                if coin_id not in _price_history:
                    _price_history[coin_id] = []
                _price_history[coin_id].append(data[coin_id]['usd'])
                if len(_price_history[coin_id]) > 60:
                    _price_history[coin_id].pop(0)

        _last_fetch = time.monotonic()
    except ImportError:
        _fetch_error = "requests library not installed"
        _last_fetch = time.monotonic()
    except Exception as e:
        _fetch_error = str(e)[:80]
        _last_fetch = time.monotonic()

# ── Drawing helpers ─────────────────────────────────────────────────
def _draw_sparkline(draw, img, x, y, w, h, data, color):
    """Draw a sparkline with gradient fill and glow effect (sysmon style)."""
    if not data or len(data) < 2:
        # Empty placeholder
        draw.rectangle([x, y, x + w, y + h], fill=(10, 13, 20))
        return
    draw.rectangle([x, y, x + w, y + h], fill=(10, 13, 20))
    mn = min(data)
    mx = max(data)
    spread = mx - mn if mx != mn else 1.0
    points = []
    for i, val in enumerate(data):
        px = x + int(i * w / (len(data) - 1))
        py = y + h - int((val - mn) * h / spread)
        py = max(y, min(y + h, py))
        points.append((px, py))

    # Gradient fill under line
    dim_fill = tuple(max(0, c - 160) for c in color)
    fill_points = points + [(x + w, y + h), (x, y + h)]
    draw.polygon(fill_points, fill=dim_fill)
    # Brighter band near the line
    brighter = tuple(max(0, c - 120) for c in color)
    band_h = max(1, h // 5)
    top_fill = []
    for px, py in points:
        top_fill.append((px, py))
    for px, py in reversed(points):
        top_fill.append((px, min(py + band_h, y + h)))
    draw.polygon(top_fill, fill=brighter)

    # Cached glow on the line
    rel_points = [(px - x, py - y) for px, py in points]
    glow_img, pad = glow_line(rel_points, w, h, color)
    img.paste(glow_img, (x - pad, y - pad), glow_img)

    # Sharp bright line on top
    draw.line(points, fill=tuple(min(255, c + 40) for c in color), width=2)

def _format_price(price):
    """Format price for display."""
    if price >= 10000:
        return f"${price:,.0f}"
    elif price >= 100:
        return f"${price:,.1f}"
    elif price >= 1:
        return f"${price:,.2f}"
    elif price >= 0.01:
        return f"${price:.4f}"
    else:
        return f"${price:.6f}"

def _draw_hero_text(draw, img, x, y, text, color, size):
    f = font(size)
    paste_hero_text(draw, img, x, y, text, color, f)

def _draw_coin_card(draw, img, x, y, w, h, coin_id, symbol):
    """Draw a single coin card with gradient panel, glowing accents, and sparkline."""
    pad = 14
    info = _prices.get(coin_id, {})
    price = info.get('usd', 0)
    change = info.get('usd_24h_change', 0)
    history = _price_history.get(coin_id, [])

    # Performance color
    if change and change >= 0:
        perf_color = GREEN
    elif change:
        perf_color = RED
    else:
        perf_color = ACCENT

    # ── Card background ──
    top_c = (14, 18, 30)
    bot_c = (8, 11, 20)
    for row in range(h):
        t = row / max(h - 1, 1)
        c = _lerp_color(top_c, bot_c, t)
        draw.line([(x, y + row), (x + w, y + row)], fill=c)

    # ── Top accent line (colored by performance) ──
    accent = glow_accent_line(w, perf_color)
    img.paste(accent, (x, y - 2), accent)

    # ── Left edge glow ──
    side = glow_side_edge(h, perf_color)
    img.paste(side, (x - 6, y), side)

    # ── Symbol (bold, top-left) ──
    draw.text((x + pad, y + 10), symbol, fill=ACCENT, font=font(38))

    # ── 24h change badge (top-right area) ──
    if change:
        chg_color = GREEN if change >= 0 else RED
        arrow = "+" if change >= 0 else ""
        chg_str = f"{arrow}{change:.1f}%"
    else:
        chg_color = TEXT_DIM
        chg_str = "---"
    draw.text((x + pad + 110, y + 16), "24h", fill=TEXT_DIM, font=font(16))
    # Change badge with background pill
    badge_f = font(22)
    badge_bbox = badge_f.getbbox(chg_str)
    badge_w = badge_bbox[2] - badge_bbox[0] + 14
    badge_h = badge_bbox[3] - badge_bbox[1] + 8
    badge_x = x + pad + 148
    badge_y = y + 12
    # Pill background
    pill_color = chg_color + (35,) if change else TEXT_DIM + (20,)
    draw.rounded_rectangle(
        [badge_x - 4, badge_y - 2, badge_x + badge_w, badge_y + badge_h],
        radius=4, fill=pill_color
    )
    draw.text((badge_x + 3, badge_y + 1), chg_str, fill=chg_color, font=badge_f)

    # ── Hero price with glow ──
    price_str = _format_price(price) if price else "---"
    _draw_hero_text(draw, img, x + pad, y + 58, price_str, TEXT_BRIGHT, 58)

    # ── Sparkline — fills remaining card space ──
    spark_y = y + 135
    spark_h = h - spark_y + y - 8
    spark_w = w - pad * 2
    if spark_h > 20 and len(history) >= 2:
        _draw_sparkline(draw, img, x + pad, spark_y, spark_w, spark_h,
                        history, perf_color)
    elif spark_h > 20:
        # No data yet — show placeholder
        draw.rectangle([x + pad, spark_y, x + pad + spark_w, spark_y + spark_h],
                       fill=(10, 13, 20))
        draw.text((x + pad + spark_w // 2 - 60, spark_y + spark_h // 2 - 10),
                  "Collecting data...", fill=TEXT_DIM, font=font(16))

# ── Main render ─────────────────────────────────────────────────────
def render_frame(w=1920, h=440):
    """Render one frame of the crypto ticker. Returns PIL Image."""
    global _scroll_offset, _last_render_time

    now = time.monotonic()

    # Initialize on first call
    if _last_render_time == 0.0:
        init()

    # Fetch prices periodically
    if now - _last_fetch >= FETCH_INTERVAL:
        _fetch_prices()

    # Calculate scroll delta
    dt = now - _last_render_time if _last_render_time > 0 else 0
    _last_render_time = now
    scroll_speed = 120  # pixels per second
    _scroll_offset += scroll_speed * dt

    # RGBA workflow — start with cached gradient background
    img = _get_bg(w, h)
    draw = ImageDraw.Draw(img)

    # Show error banner if needed
    if _fetch_error and not _prices:
        _draw_hero_text(draw, img, 40, 40, "CRYPTO TICKER", ACCENT, 32)
        draw.text((40, 90), f"Error: {_fetch_error}", fill=RED, font=font(24))
        draw.text((40, 130), "Retrying every 60 seconds...", fill=TEXT_DIM, font=font(18))
        # Bottom glow line
        for i in range(8):
            a = int(180 * (1.0 - i / 8))
            draw.line([(0, h - 1 - i), (w, h - 1 - i)], fill=ACCENT + (a,))
        # Scanlines + convert
        img = Image.alpha_composite(img, _get_scanlines(w, h))
        out = Image.new('RGB', (w, h), BG)
        out.paste(img, (0, 0), img)
        return out

    # Card dimensions — leave room for status bar at bottom
    card_w = 320
    card_h = h - 44
    card_gap = 24
    card_y = 8
    num_coins = len(COINS)
    total_strip_w = num_coins * (card_w + card_gap)

    # Calculate scroll position (wraps around)
    scroll_pos = _scroll_offset % total_strip_w

    # Draw coins in scrolling strip
    for copy in range(-1, (w // total_strip_w) + 3):
        base_x = copy * total_strip_w - scroll_pos
        for i, (coin_id, symbol) in enumerate(COINS):
            cx = base_x + i * (card_w + card_gap)
            # Only draw if card is visible (with margin)
            if cx + card_w < -card_w or cx > w + card_w:
                continue
            _draw_coin_card(draw, img, int(cx), card_y, card_w, card_h,
                            coin_id, symbol)

    # ── Bottom status bar — readable ──
    draw.text((12, h - 34), "CRYPTO TICKER", fill=ACCENT, font=font(18))
    if _last_fetch > 0:
        ago = int(now - _last_fetch)
        draw.text((w - 240, h - 34), f"Updated {ago}s ago", fill=TEXT, font=font(18))
    if _fetch_error:
        draw.text((w // 2 - 120, h - 34), f"API: {_fetch_error[:40]}",
                  fill=YELLOW, font=font(18))

    # Glowing bottom accent line
    for i in range(8):
        a = int(180 * (1.0 - i / 8))
        draw.line([(0, h - 1 - i), (w, h - 1 - i)], fill=ACCENT + (a,))

    # Apply scanlines and convert RGBA -> RGB
    img = Image.alpha_composite(img, _get_scanlines(w, h))
    out = Image.new('RGB', (w, h), BG)
    out.paste(img, (0, 0), img)
    return out


# ── Standalone mode ─────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    if '--once' in sys.argv:
        init()
        time.sleep(0.1)
        img = render_frame()
        img.save('/tmp/ticker.png')
        print("Saved to /tmp/ticker.png")
    else:
        print("Usage:")
        print("  python3 ticker.py --once   # save one frame to /tmp/ticker.png")
        print("  Use via tinyscreen daemon for live display")
