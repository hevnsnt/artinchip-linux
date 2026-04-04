"""Internet speed test dashboard for tinyscreen bar display.

Runs periodic speed tests via speedtest-cli in the background.
Shows download/upload/ping with historical sparklines.

Requires: pip install speedtest-cli
"""

import os
import time
import threading
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── Colors ─────────────────────────────────────────────────────────
BG          = (5, 7, 12)
ACCENT      = (0, 210, 255)
TEXT        = (220, 225, 240)
TEXT_DIM    = (65, 75, 100)
TEXT_BRIGHT = (252, 254, 255)
GREEN       = (0, 255, 140)
RED         = (255, 50, 50)
YELLOW      = (255, 225, 0)
ORANGE      = (255, 165, 30)
CYAN        = (0, 240, 255)
PURPLE      = (160, 110, 255)
DL_COLOR    = (0, 220, 255)   # download = cyan-blue
UL_COLOR    = (160, 80, 255)  # upload = purple
PING_COLOR  = (0, 255, 160)   # ping = green

_fonts = {}
def font(size):
    if size not in _fonts:
        for path in ['/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
                     '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
            try:
                _fonts[size] = ImageFont.truetype(path, size)
                return _fonts[size]
            except Exception:
                continue
        _fonts[size] = ImageFont.load_default()
    return _fonts[size]

def _lerp_color(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

# ── Background ─────────────────────────────────────────────────────
_bg_cache = {}
def _get_bg(w, h):
    if (w, h) not in _bg_cache:
        img = Image.new('RGBA', (w, h), BG + (255,))
        draw = ImageDraw.Draw(img)
        for y in range(h):
            t = y / max(h - 1, 1)
            c = _lerp_color((8, 12, 22), (3, 5, 10), t)
            draw.line([(0, y), (w, y)], fill=c)
        for gx in range(0, w, 60):
            draw.line([(gx, 0), (gx, h)], fill=(15, 20, 32, 35))
        for gy in range(0, h, 40):
            draw.line([(0, gy), (w, gy)], fill=(15, 20, 32, 35))
        for i in range(60):
            a = int(25 * (1.0 - i / 60))
            draw.line([(0, h-1-i), (w, h-1-i)], fill=(0, 80, 160, a))
        vig = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        vd = ImageDraw.Draw(vig)
        for i in range(50):
            a = int(60 * (1.0 - i / 50))
            vd.line([(0, i), (w, i)], fill=(0, 0, 0, a))
            vd.line([(0, h-1-i), (w, h-1-i)], fill=(0, 0, 0, a))
        for i in range(80):
            a = int(50 * (1.0 - i / 80))
            vd.line([(i, 0), (i, h)], fill=(0, 0, 0, a))
            vd.line([(w-1-i, 0), (w-1-i, h)], fill=(0, 0, 0, a))
        img = Image.alpha_composite(img, vig)
        _bg_cache[(w, h)] = img
    return _bg_cache[(w, h)].copy()

_scanline_cache = {}
def _get_scanlines(w, h):
    if (w, h) not in _scanline_cache:
        sl = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sl)
        for y in range(0, h, 3):
            sd.line([(0, y), (w, y)], fill=(0, 0, 0, 55))
        _scanline_cache[(w, h)] = sl
    return _scanline_cache[(w, h)]

def _hero(draw, img, x, y, text, color, size):
    f = font(size)
    bbox = f.getbbox(text)
    tw = bbox[2] - bbox[0] + 20
    th = bbox[3] - bbox[1] + 20
    pad = 12
    glow = Image.new('RGBA', (tw + pad*2, th + pad*2), (0,0,0,0))
    gd = ImageDraw.Draw(glow)
    gd.text((pad - bbox[0], pad - bbox[1]), text, fill=color+(110,), font=f)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=8))
    img.paste(glow, (x - pad, y - pad), glow)
    draw.text((x, y), text, fill=color, font=f)

def _draw_sparkline(draw, img, x, y, w, h, data, color, filled=True):
    if not data or len(data) < 2:
        draw.rectangle([x, y, x + w, y + h], fill=(6, 8, 14))
        return
    draw.rectangle([x, y, x + w, y + h], fill=(6, 8, 14))
    mx = max(data) or 1
    points = []
    for i, val in enumerate(data):
        px = x + int(i * w / (len(data) - 1))
        py = y + h - int(min(val, mx) * h / mx)
        points.append((px, py))
    if filled:
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            for px in range(x1, x2 + 1):
                t = (px - x) / max(w, 1)
                col = _lerp_color(tuple(max(0, c - 120) for c in color),
                                  tuple(max(0, c - 60) for c in color), t)
                frac = (px - x1) / max(x2 - x1, 1)
                py_interp = int(y1 + (y2 - y1) * frac)
                draw.line([(px, py_interp), (px, y + h)], fill=col)
    # Glow
    pad = 8
    gw, gh = w + pad*2, h + pad*2
    glow = Image.new('RGBA', (gw, gh), (0,0,0,0))
    gd = ImageDraw.Draw(glow)
    shifted = [(px - x + pad, py - y + pad) for px, py in points]
    gd.line(shifted, fill=color+(150,), width=5)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=6))
    img.paste(glow, (x - pad, y - pad), glow)
    draw.line(points, fill=tuple(min(255, c + 40) for c in color), width=2)

def _draw_glow_bar(draw, img, x, y, w, h, pct, color):
    draw.rectangle([x, y, x + w, y + h], fill=(12, 15, 22))
    fill_w = max(0, int(w * min(pct, 100.0) / 100.0))
    if fill_w <= 0:
        return
    pad = 10
    gw, gh = fill_w + pad*2, h + pad*2
    glow = Image.new('RGBA', (gw, gh), (0,0,0,0))
    gd = ImageDraw.Draw(glow)
    gd.rectangle([pad, pad, pad + fill_w, pad + h], fill=color+(100,))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=8))
    img.paste(glow, (x - pad, y - pad), glow)
    for col in range(fill_w):
        t = col / max(fill_w - 1, 1)
        c = _lerp_color(tuple(max(0, v - 80) for v in color), color, t * 0.8 + 0.2)
        draw.line([(x+1+col, y+1), (x+1+col, y+h-1)], fill=c)
    tip_w = min(4, fill_w)
    bright = tuple(min(255, c + 60) for c in color)
    draw.rectangle([x + fill_w - tip_w, y, x + fill_w, y + h], fill=bright)

# ── State ──────────────────────────────────────────────────────────
_cache = {
    'download': 0,      # Mbps
    'upload': 0,        # Mbps
    'ping': 0,          # ms
    'server': '',       # server name
    'isp': '',
    'dl_history': [],   # list of download speeds
    'ul_history': [],   # list of upload speeds
    'ping_history': [], # list of ping times
    'last_test': 0,
    'testing': False,
    'phase': '',        # 'server', 'download', 'upload', 'done'
    'progress': 0,      # 0-100 for current phase
    'error': None,
    'test_count': 0,
}

TEST_INTERVAL = 15 * 60  # 15 minutes between tests


def _do_test():
    """Run speed test in background."""
    try:
        import speedtest
        _cache['phase'] = 'server'
        _cache['progress'] = 0

        st = speedtest.Speedtest()
        st.get_best_server()
        _cache['server'] = f"{st.best['sponsor']} ({st.best['name']})"
        _cache['ping'] = st.best['latency']

        _cache['phase'] = 'download'
        _cache['progress'] = 0
        st.download()
        _cache['download'] = st.results.download / 1_000_000

        _cache['phase'] = 'upload'
        _cache['progress'] = 0
        st.upload()
        _cache['upload'] = st.results.upload / 1_000_000

        _cache['isp'] = st.results.client.get('isp', '')

        # Store history
        _cache['dl_history'].append(_cache['download'])
        _cache['ul_history'].append(_cache['upload'])
        _cache['ping_history'].append(_cache['ping'])
        # Keep last 24 results
        for k in ('dl_history', 'ul_history', 'ping_history'):
            if len(_cache[k]) > 24:
                _cache[k] = _cache[k][-24:]

        _cache['phase'] = 'done'
        _cache['test_count'] += 1
        _cache['error'] = None

    except ImportError:
        _cache['error'] = 'speedtest-cli not installed (pip install speedtest-cli)'
    except Exception as e:
        _cache['error'] = str(e)[:80]
    finally:
        _cache['testing'] = False
        _cache['last_test'] = time.time()


def _run_test():
    now = time.time()
    if now - _cache['last_test'] < TEST_INTERVAL and _cache['test_count'] > 0:
        return
    if _cache['testing']:
        return
    _cache['testing'] = True
    threading.Thread(target=_do_test, daemon=True).start()


def init():
    _cache['last_test'] = 0
    _cache['test_count'] = 0
    _cache['dl_history'] = []
    _cache['ul_history'] = []
    _cache['ping_history'] = []
    _run_test()


# ── Render ─────────────────────────────────────────────────────────
def render_frame(w=1920, h=440):
    _run_test()
    img = _get_bg(w, h)
    draw = ImageDraw.Draw(img)

    # ── Testing in progress ──
    if _cache['testing'] or (_cache['test_count'] == 0 and not _cache['error']):
        draw.text((30, 15), "SPEED TEST", fill=ACCENT, font=font(28))

        phase = _cache['phase']
        if phase == 'server':
            msg = "Finding best server..."
            pct = 15
        elif phase == 'download':
            msg = "Testing download speed..."
            pct = 45
        elif phase == 'upload':
            msg = "Testing upload speed..."
            pct = 75
        else:
            msg = "Connecting..."
            pct = 5

        # Animated dots
        dots = '.' * (int(time.time() * 2) % 4)
        draw.text((w // 2 - 180, h // 2 - 40), msg + dots, fill=TEXT_BRIGHT, font=font(30))

        # Progress bar
        bar_w = w - 200
        bar_x = 100
        bar_y = h // 2 + 20
        _draw_glow_bar(draw, img, bar_x, bar_y, bar_w, 20, pct,
                       DL_COLOR if phase == 'download' else
                       UL_COLOR if phase == 'upload' else ACCENT)
        draw = ImageDraw.Draw(img)
        draw.text((bar_x + bar_w + 10, bar_y), f"{pct}%", fill=TEXT, font=font(18))

        # Show partial results as they come in
        if _cache['ping'] > 0:
            draw.text((30, h - 50), f"Ping: {_cache['ping']:.0f}ms", fill=PING_COLOR, font=font(22))
        if _cache['download'] > 0 and phase != 'download':
            draw.text((300, h - 50), f"Download: {_cache['download']:.1f} Mbps",
                      fill=DL_COLOR, font=font(22))
        if _cache['server']:
            draw.text((30, h - 25), _cache['server'], fill=TEXT_DIM, font=font(16))

        img = Image.alpha_composite(img, _get_scanlines(w, h))
        out = Image.new('RGB', (w, h), BG)
        out.paste(img, (0, 0), img)
        return out

    # ── Error state ──
    if _cache['error'] and _cache['test_count'] == 0:
        draw.text((30, 20), "SPEED TEST", fill=ACCENT, font=font(28))
        draw.text((30, 70), f"Error: {_cache['error']}", fill=RED, font=font(22))
        img = Image.alpha_composite(img, _get_scanlines(w, h))
        out = Image.new('RGB', (w, h), BG)
        out.paste(img, (0, 0), img)
        return out

    # ═══════════════════════════════════════════════════════════════
    # RESULTS DASHBOARD
    # ═══════════════════════════════════════════════════════════════

    dl = _cache['download']
    ul = _cache['upload']
    ping = _cache['ping']

    # ── Top: Hero metrics ──
    draw.text((30, 6), "SPEED TEST", fill=ACCENT, font=font(22))

    # Time since last test
    ago = int(time.time() - _cache['last_test'])
    if ago < 60:
        ago_str = f"{ago}s ago"
    elif ago < 3600:
        ago_str = f"{ago // 60}m ago"
    else:
        ago_str = f"{ago // 3600}h ago"
    draw.text((200, 10), ago_str, fill=TEXT_DIM, font=font(16))

    # Server + ISP
    if _cache['server']:
        draw.text((320, 10), _cache['server'], fill=TEXT_DIM, font=font(16))

    # DOWNLOAD — massive
    dx = 30
    draw.text((dx, 38), "DOWNLOAD", fill=DL_COLOR, font=font(18))
    _hero(draw, img, dx, 62, f"{dl:.1f}", DL_COLOR, 70)
    draw = ImageDraw.Draw(img)
    draw.text((dx + font(70).getlength(f"{dl:.1f}") + 10, 90), "Mbps",
              fill=DL_COLOR, font=font(24))

    # UPLOAD — massive
    ux = 420
    draw.text((ux, 38), "UPLOAD", fill=UL_COLOR, font=font(18))
    _hero(draw, img, ux, 62, f"{ul:.1f}", UL_COLOR, 70)
    draw = ImageDraw.Draw(img)
    draw.text((ux + font(70).getlength(f"{ul:.1f}") + 10, 90), "Mbps",
              fill=UL_COLOR, font=font(24))

    # PING
    px_start = 780
    draw.text((px_start, 38), "PING", fill=PING_COLOR, font=font(18))
    ping_color = GREEN if ping < 30 else YELLOW if ping < 80 else RED
    _hero(draw, img, px_start, 62, f"{ping:.0f}", ping_color, 70)
    draw = ImageDraw.Draw(img)
    draw.text((px_start + font(70).getlength(f"{ping:.0f}") + 10, 90), "ms",
              fill=ping_color, font=font(24))

    # Speed rating
    rx = 1050
    if dl >= 100:
        rating, r_color = "EXCELLENT", GREEN
    elif dl >= 50:
        rating, r_color = "GOOD", CYAN
    elif dl >= 25:
        rating, r_color = "FAIR", YELLOW
    elif dl >= 10:
        rating, r_color = "SLOW", ORANGE
    else:
        rating, r_color = "POOR", RED
    draw.text((rx, 38), "RATING", fill=TEXT, font=font(18))
    _hero(draw, img, rx, 62, rating, r_color, 50)
    draw = ImageDraw.Draw(img)

    # Next test countdown
    next_test = max(0, TEST_INTERVAL - (time.time() - _cache['last_test']))
    next_min = int(next_test // 60)
    draw.text((rx + 300, 50), "NEXT TEST", fill=TEXT_DIM, font=font(16))
    draw.text((rx + 300, 72), f"{next_min}m", fill=TEXT, font=font(36))

    # ── Accent line ──
    line_y = 145
    glow_line = Image.new('RGBA', (w, 10), (0,0,0,0))
    gld = ImageDraw.Draw(glow_line)
    gld.rectangle([0, 3, w, 5], fill=ACCENT + (120,))
    glow_line = glow_line.filter(ImageFilter.GaussianBlur(radius=3))
    img.paste(glow_line, (0, line_y), glow_line)
    draw = ImageDraw.Draw(img)

    # ── Middle: Download + Upload sparklines (side by side) ──
    spark_y = line_y + 14
    spark_h = 150
    half_w = (w - 80) // 2

    # Download history
    draw.text((30, spark_y - 2), "DOWNLOAD HISTORY", fill=DL_COLOR, font=font(14))
    if _cache['dl_history']:
        avg_dl = sum(_cache['dl_history']) / len(_cache['dl_history'])
        draw.text((250, spark_y - 2), f"avg {avg_dl:.1f} Mbps", fill=TEXT_DIM, font=font(14))
    _draw_sparkline(draw, img, 30, spark_y + 16, half_w, spark_h,
                    _cache['dl_history'], DL_COLOR)
    draw = ImageDraw.Draw(img)

    # Upload history
    ul_x = 30 + half_w + 20
    draw.text((ul_x, spark_y - 2), "UPLOAD HISTORY", fill=UL_COLOR, font=font(14))
    if _cache['ul_history']:
        avg_ul = sum(_cache['ul_history']) / len(_cache['ul_history'])
        draw.text((ul_x + 220, spark_y - 2), f"avg {avg_ul:.1f} Mbps", fill=TEXT_DIM, font=font(14))
    _draw_sparkline(draw, img, ul_x, spark_y + 16, half_w, spark_h,
                    _cache['ul_history'], UL_COLOR)
    draw = ImageDraw.Draw(img)

    # ── Bottom: Ping history bar ──
    ping_y = spark_y + spark_h + 30
    draw.text((30, ping_y), "PING HISTORY", fill=PING_COLOR, font=font(14))
    if _cache['ping_history']:
        avg_ping = sum(_cache['ping_history']) / len(_cache['ping_history'])
        draw.text((200, ping_y), f"avg {avg_ping:.0f}ms", fill=TEXT_DIM, font=font(14))
    # Draw ping as thin sparkline
    _draw_sparkline(draw, img, 30, ping_y + 16, w - 60, 30,
                    _cache['ping_history'], PING_COLOR, filled=False)
    draw = ImageDraw.Draw(img)

    # Bottom accent
    for i in range(6):
        a = int(160 * (1.0 - i / 6))
        draw.line([(0, h-1-i), (w, h-1-i)], fill=ACCENT + (a,))

    img = Image.alpha_composite(img, _get_scanlines(w, h))
    out = Image.new('RGB', (w, h), BG)
    out.paste(img, (0, 0), img)
    return out


if __name__ == '__main__':
    import sys
    if '--once' in sys.argv:
        init()
        # Wait for test to complete
        while _cache['testing']:
            time.sleep(1)
            print(f"  {_cache['phase']}...")
        render_frame().save('/tmp/speedtest.png')
        print("Saved to /tmp/speedtest.png")
    else:
        print("Usage: python3 speedtest_mode.py --once")
