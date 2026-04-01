#!/usr/bin/env python3
"""
tinyscreen crypto ticker — scrolling cryptocurrency prices with sparklines.

Fetches from CoinGecko free API (no key required).
Designed for 1920x440 stretched bar LCDs.
"""

import time
from PIL import Image, ImageDraw, ImageFont

# ── Colors ──────────────────────────────────────────────────────────
BG         = (10, 12, 18)
PANEL_BG   = (18, 22, 32)
BORDER     = (35, 42, 58)
ACCENT     = (0, 170, 255)
TEXT       = (200, 210, 225)
TEXT_DIM   = (100, 110, 130)
GREEN      = (0, 220, 100)
RED        = (255, 60, 60)
YELLOW     = (255, 200, 0)
ORANGE     = (255, 140, 0)
CYAN       = (0, 220, 220)

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
def _draw_sparkline(draw, x, y, w, h, data, color):
    """Draw a mini sparkline graph."""
    if not data or len(data) < 2:
        # Draw empty placeholder
        draw.rectangle([x, y, x + w, y + h], fill=(12, 14, 22), outline=BORDER)
        return
    draw.rectangle([x, y, x + w, y + h], fill=(12, 14, 22))
    mn = min(data)
    mx = max(data)
    spread = mx - mn if mx != mn else 1.0
    points = []
    for i, val in enumerate(data):
        px = x + int(i * w / (len(data) - 1))
        py = y + h - int((val - mn) * h / spread)
        py = max(y, min(y + h, py))
        points.append((px, py))
    # Fill under the line
    fill_points = points + [(x + w, y + h), (x, y + h)]
    fill_color = tuple(max(0, c - 180) for c in color)
    draw.polygon(fill_points, fill=fill_color)
    # Draw the line
    draw.line(points, fill=color, width=2)

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

def _draw_coin_card(draw, x, y, w, h, coin_id, symbol):
    """Draw a single coin card at position (x, y). Returns card width used."""
    pad = 12
    info = _prices.get(coin_id, {})
    price = info.get('usd', 0)
    change = info.get('usd_24h_change', 0)
    history = _price_history.get(coin_id, [])

    # Card background
    draw.rectangle([x, y, x + w, y + h], fill=PANEL_BG, outline=BORDER)
    # Top accent
    draw.rectangle([x, y, x + w, y + 2], fill=ACCENT)

    # Symbol (large, top-left)
    draw.text((x + pad, y + 10), symbol, fill=ACCENT, font=font(38))

    # Price (large, right of symbol)
    price_str = _format_price(price) if price else "---"
    draw.text((x + pad, y + 58), price_str, fill=TEXT, font=font(52))

    # 24h change
    if change:
        chg_color = GREEN if change >= 0 else RED
        arrow = "+" if change >= 0 else ""
        chg_str = f"{arrow}{change:.1f}%"
    else:
        chg_color = TEXT_DIM
        chg_str = "---"
    draw.text((x + pad + 110, y + 16), "24h", fill=TEXT_DIM, font=font(16))
    draw.text((x + pad + 145, y + 12), chg_str, fill=chg_color, font=font(24))

    # Sparkline — bottom portion of card
    spark_y = y + 125
    spark_h = h - 145
    spark_w = w - pad * 2
    if spark_h > 20:
        spark_color = GREEN if change and change >= 0 else RED if change else TEXT_DIM
        _draw_sparkline(draw, x + pad, spark_y, spark_w, spark_h, history, spark_color)

    # 24h label under sparkline
    if spark_h > 20 and history:
        draw.text((x + pad, spark_y + spark_h + 4),
                  f"Last {len(history)} fetches", fill=TEXT_DIM, font=font(12))

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

    img = Image.new('RGB', (w, h), BG)
    draw = ImageDraw.Draw(img)

    # Show error banner if needed
    if _fetch_error and not _prices:
        # Full-screen error
        draw.rectangle([0, 0, w, h], fill=BG)
        draw.text((40, 40), "CRYPTO TICKER", fill=ACCENT, font=font(32))
        draw.text((40, 90), f"Error: {_fetch_error}", fill=RED, font=font(24))
        draw.text((40, 130), "Retrying every 60 seconds...", fill=TEXT_DIM, font=font(18))
        # Bottom accent
        draw.rectangle([0, h - 2, w, h], fill=(0, 80, 140))
        return img

    # Card dimensions
    card_w = 320
    card_h = h - 30  # top and bottom margin
    card_gap = 24
    card_y = 12
    num_coins = len(COINS)
    total_strip_w = num_coins * (card_w + card_gap)

    # Calculate scroll position (wraps around)
    scroll_pos = _scroll_offset % total_strip_w

    # Draw header bar with status info
    # Top-left: title
    draw.text((10, h - 22), "CRYPTO TICKER", fill=TEXT_DIM, font=font(13))
    # Top-right: last update time
    if _last_fetch > 0:
        ago = int(now - _last_fetch)
        draw.text((w - 200, h - 22), f"Updated {ago}s ago", fill=TEXT_DIM, font=font(13))
    if _fetch_error:
        draw.text((w // 2 - 100, h - 22), f"API: {_fetch_error[:40]}",
                  fill=YELLOW, font=font(13))

    # Draw coins in scrolling strip
    # We draw enough copies to fill the screen plus overflow
    for copy in range(-1, (w // total_strip_w) + 3):
        base_x = copy * total_strip_w - scroll_pos
        for i, (coin_id, symbol) in enumerate(COINS):
            cx = base_x + i * (card_w + card_gap)
            # Only draw if card is visible (with margin)
            if cx + card_w < -card_w or cx > w + card_w:
                continue
            _draw_coin_card(draw, int(cx), card_y, card_w, card_h, coin_id, symbol)

    # Bottom accent line
    draw.rectangle([0, h - 2, w, h], fill=(0, 80, 140))

    return img


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
