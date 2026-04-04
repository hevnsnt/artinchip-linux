"""DNS ad-blocker dashboard for tinyscreen bar display.

Supports AdGuard Home and Pi-hole. Full-width immersive layout with
massive timeline, hero metrics, and top blocked domains.

Config: ADGUARD_URL, ADGUARD_USER, ADGUARD_PASS (or PIHOLE_URL, PIHOLE_API_KEY)
"""

import math
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
SHIELD_GREEN = (0, 220, 110)
BLOCK_RED    = (255, 60, 80)

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

# ── Background (cached) ───────────────────────────────────────────
_bg_cache = {}
def _get_bg(w, h):
    if (w, h) not in _bg_cache:
        img = Image.new('RGBA', (w, h), BG + (255,))
        draw = ImageDraw.Draw(img)
        for y in range(h):
            t = y / max(h - 1, 1)
            c = _lerp_color((8, 12, 22), (3, 5, 10), t)
            draw.line([(0, y), (w, y)], fill=c)
        # Subtle grid
        for gx in range(0, w, 60):
            draw.line([(gx, 0), (gx, h)], fill=(15, 20, 32, 35))
        for gy in range(0, h, 40):
            draw.line([(0, gy), (w, gy)], fill=(15, 20, 32, 35))
        # Bottom glow
        for i in range(60):
            a = int(25 * (1.0 - i / 60))
            draw.line([(0, h-1-i), (w, h-1-i)], fill=(0, 80, 160, a))
        # Vignette
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

# ── API backends ───────────────────────────────────────────────────
def _fetch_adguard(url, user, passwd):
    import requests
    auth = (user, passwd) if user else None
    data = {}
    try:
        r = requests.get(f'{url}/control/status', auth=auth, timeout=5)
        r.raise_for_status()
        s = r.json()
        data['protection'] = s.get('protection_enabled', False)
        data['version'] = s.get('version', '')
        r = requests.get(f'{url}/control/stats', auth=auth, timeout=5)
        r.raise_for_status()
        st = r.json()
        data['total'] = st.get('num_dns_queries', 0)
        data['blocked'] = st.get('num_blocked_filtering', 0)
        data['pct'] = data['blocked'] / data['total'] * 100 if data['total'] else 0
        data['ms'] = st.get('avg_processing_time', 0) * 1000
        data['top_blocked'] = st.get('top_blocked_domains', [])[:12]
        data['top_clients'] = st.get('top_clients', [])[:6]
        data['q_time'] = st.get('dns_queries', [])
        data['b_time'] = st.get('blocked_filtering', [])
        data['backend'] = 'AdGuard'
        data['error'] = None
    except Exception as e:
        data['error'] = str(e)[:80]
    return data

def _fetch_pihole(url, api_key):
    import requests
    data = {}
    try:
        p = {'auth': api_key} if api_key else {}
        r = requests.get(f'{url}/admin/api.php?summary', params=p, timeout=5)
        r.raise_for_status()
        s = r.json()
        data['total'] = int(s.get('dns_queries_today', 0))
        data['blocked'] = int(s.get('ads_blocked_today', 0))
        data['pct'] = float(s.get('ads_percentage_today', 0))
        data['protection'] = s.get('status') == 'enabled'
        data['ms'] = 0
        data['top_blocked'] = []
        data['top_clients'] = []
        data['q_time'] = []
        data['b_time'] = []
        data['backend'] = 'Pi-hole'
        data['error'] = None
    except Exception as e:
        data['error'] = str(e)[:80]
    return data

# ── State ──────────────────────────────────────────────────────────
_cache = {'data': {}, 'last_fetch': 0, 'fetching': False, 'error': None}

def _detect():
    ag = os.environ.get('ADGUARD_URL', 'http://192.168.1.50')
    u = os.environ.get('ADGUARD_USER', 'admin')
    p = os.environ.get('ADGUARD_PASS', '')
    ph = os.environ.get('PIHOLE_URL', '')
    pk = os.environ.get('PIHOLE_API_KEY', '')
    return ('pihole', ph, pk, '', '') if ph else ('adguard', ag, '', u, p)

def _do_fetch():
    b, url, key, u, p = _detect()
    try:
        _cache['data'] = _fetch_pihole(url, key) if b == 'pihole' else _fetch_adguard(url, u, p)
        _cache['error'] = _cache['data'].get('error')
    except Exception as e:
        _cache['error'] = str(e)[:80]
    finally:
        _cache['fetching'] = False
        _cache['last_fetch'] = time.time()

def _fetch():
    now = time.time()
    if now - _cache['last_fetch'] < 10 or _cache['fetching']:
        return
    _cache['fetching'] = True
    threading.Thread(target=_do_fetch, daemon=True).start()

def init():
    _cache['last_fetch'] = 0
    _cache['data'] = {}
    _fetch()

def _fmt(n):
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000: return f"{n/1_000:.1f}K"
    return str(int(n))

# ── Render ─────────────────────────────────────────────────────────
def render_frame(w=1920, h=440):
    _fetch()
    img = _get_bg(w, h)
    draw = ImageDraw.Draw(img)
    d = _cache['data']

    # ── Auth/error ──
    if not d or (_cache['error'] and not d.get('total')):
        err = _cache['error'] or ''
        draw.text((30, 20), "DNS BLOCKER", fill=ACCENT, font=font(30))
        if '401' in str(err):
            draw.text((30, 70), "Authentication required", fill=YELLOW, font=font(26))
            draw.text((30, 120), "ADGUARD_PASS=password tinyscreen --pihole", fill=CYAN, font=font(22))
        elif _cache['fetching']:
            dots = '.' * (int(time.time() * 2) % 4)
            draw.text((w//2 - 100, h//2 - 15), f"Connecting{dots}", fill=ACCENT, font=font(30))
        else:
            draw.text((30, 70), f"Error: {err}", fill=RED, font=font(22))
        img = Image.alpha_composite(img, _get_scanlines(w, h))
        return Image.alpha_composite(Image.new('RGBA', (w, h), BG+(255,)), img).convert('RGB')

    total = d.get('total', 0)
    blocked = d.get('blocked', 0)
    pct = d.get('pct', 0)
    ms = d.get('ms', 0)
    protected = d.get('protection', False)
    q_time = d.get('q_time', [])
    b_time = d.get('b_time', [])

    # ═══════════════════════════════════════════════════════════════
    # TOP SECTION: Hero metrics bar (full width, ~100px)
    # ═══════════════════════════════════════════════════════════════
    # Protection status indicator — glowing dot
    dot_color = SHIELD_GREEN if protected else RED
    dot_x, dot_y = 30, 22
    # Glow
    glow = Image.new('RGBA', (40, 40), (0,0,0,0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([5, 5, 35, 35], fill=dot_color + (60,))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=6))
    img.paste(glow, (dot_x - 8, dot_y - 8), glow)
    draw = ImageDraw.Draw(img)
    draw.ellipse([dot_x, dot_y, dot_x + 20, dot_y + 20], fill=dot_color)
    bright = tuple(min(255, c + 80) for c in dot_color)
    draw.ellipse([dot_x + 5, dot_y + 5, dot_x + 15, dot_y + 15], fill=bright)

    draw.text((dot_x + 30, dot_y - 2), d.get('backend', 'DNS'), fill=ACCENT, font=font(24))
    status = "PROTECTED" if protected else "DISABLED"
    draw.text((dot_x + 30, dot_y + 24), status, fill=dot_color, font=font(16))

    # Hero metrics spread across the top
    # TOTAL QUERIES
    mx = 220
    draw.text((mx, 8), "QUERIES", fill=TEXT_DIM, font=font(16))
    _hero(draw, img, mx, 28, _fmt(total), ACCENT, 52)
    draw = ImageDraw.Draw(img)

    # BLOCKED
    mx = 520
    draw.text((mx, 8), "BLOCKED", fill=TEXT_DIM, font=font(16))
    _hero(draw, img, mx, 28, _fmt(blocked), BLOCK_RED, 52)
    draw = ImageDraw.Draw(img)

    # BLOCK RATE
    mx = 780
    draw.text((mx, 8), "BLOCK RATE", fill=TEXT_DIM, font=font(16))
    rate_color = RED if pct > 15 else ORANGE if pct > 5 else GREEN
    _hero(draw, img, mx, 28, f"{pct:.1f}%", rate_color, 52)
    draw = ImageDraw.Draw(img)

    # RESPONSE TIME
    mx = 1050
    draw.text((mx, 8), "RESPONSE", fill=TEXT_DIM, font=font(16))
    ms_color = GREEN if ms < 30 else YELLOW if ms < 80 else RED
    _hero(draw, img, mx, 28, f"{ms:.0f}ms", ms_color, 52)
    draw = ImageDraw.Draw(img)

    # ALLOWED
    mx = 1300
    allowed = total - blocked
    draw.text((mx, 8), "ALLOWED", fill=TEXT_DIM, font=font(16))
    _hero(draw, img, mx, 28, _fmt(allowed), GREEN, 52)
    draw = ImageDraw.Draw(img)

    # Thin accent line under hero metrics
    line_y = 95
    glow_line = Image.new('RGBA', (w, 12), (0,0,0,0))
    gld = ImageDraw.Draw(glow_line)
    gld.rectangle([0, 4, w, 6], fill=ACCENT + (150,))
    gld.rectangle([0, 6, w, 8], fill=ACCENT + (40,))
    glow_line = glow_line.filter(ImageFilter.GaussianBlur(radius=3))
    img.paste(glow_line, (0, line_y), glow_line)
    draw = ImageDraw.Draw(img)

    # ═══════════════════════════════════════════════════════════════
    # MIDDLE: Full-width timeline (the visual centerpiece)
    # ═══════════════════════════════════════════════════════════════
    spark_x = 30
    spark_y = line_y + 16
    spark_w = w - 60
    spark_h = 160  # BIG

    if q_time and len(q_time) >= 2:
        # Dark background for chart
        draw.rectangle([spark_x, spark_y, spark_x + spark_w, spark_y + spark_h],
                      fill=(6, 8, 14))

        mx_val = max(q_time) or 1
        n = len(q_time)

        # Gradient fill for queries
        points = []
        for i, val in enumerate(q_time):
            px = spark_x + int(i * spark_w / (n - 1))
            py = spark_y + spark_h - int(min(val, mx_val) * spark_h / mx_val)
            points.append((px, py))

        # Gradient fill — column by column for smooth color
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            for px in range(x1, x2 + 1):
                t = (px - spark_x) / max(spark_w, 1)
                col = _lerp_color((0, 40, 80), (0, 120, 200), t)
                # Interpolate y
                if x2 != x1:
                    frac = (px - x1) / (x2 - x1)
                else:
                    frac = 0
                py = int(y1 + (y2 - y1) * frac)
                draw.line([(px, py), (px, spark_y + spark_h)], fill=col)

        # Glow on the query line
        pad = 10
        gw, gh = spark_w + pad*2, spark_h + pad*2
        glow = Image.new('RGBA', (gw, gh), (0,0,0,0))
        gd = ImageDraw.Draw(glow)
        shifted = [(px - spark_x + pad, py - spark_y + pad) for px, py in points]
        gd.line(shifted, fill=ACCENT + (160,), width=5)
        glow = glow.filter(ImageFilter.GaussianBlur(radius=6))
        img.paste(glow, (spark_x - pad, spark_y - pad), glow)
        draw = ImageDraw.Draw(img)

        # Sharp query line
        draw.line(points, fill=ACCENT, width=2)

        # Blocked overlay (red area at bottom)
        if b_time and len(b_time) >= 2:
            b_points = []
            for i, val in enumerate(b_time):
                px = spark_x + int(i * spark_w / (len(b_time) - 1))
                # Scale relative to same max
                py = spark_y + spark_h - int(min(val, mx_val) * spark_h / mx_val)
                b_points.append((px, py))
            # Red fill
            fill_pts = b_points + [(spark_x + spark_w, spark_y + spark_h),
                                    (spark_x, spark_y + spark_h)]
            fill_color = (60, 15, 20)
            draw.polygon(fill_pts, fill=fill_color)
            draw.line(b_points, fill=BLOCK_RED, width=2)

        # Legend
        draw.text((spark_x + 4, spark_y + 4), "24h", fill=TEXT_DIM, font=font(14))
        draw.rectangle([spark_x + 40, spark_y + 8, spark_x + 52, spark_y + 14], fill=ACCENT)
        draw.text((spark_x + 58, spark_y + 4), "queries", fill=TEXT_DIM, font=font(14))
        draw.rectangle([spark_x + 130, spark_y + 8, spark_x + 142, spark_y + 14], fill=BLOCK_RED)
        draw.text((spark_x + 148, spark_y + 4), "blocked", fill=TEXT_DIM, font=font(14))
    else:
        draw.rectangle([spark_x, spark_y, spark_x + spark_w, spark_y + spark_h],
                      fill=(6, 8, 14))
        draw.text((spark_x + spark_w//2 - 80, spark_y + spark_h//2 - 10),
                  "Collecting data...", fill=TEXT_DIM, font=font(20))

    # ═══════════════════════════════════════════════════════════════
    # BOTTOM: Top blocked domains + top clients (split)
    # ═══════════════════════════════════════════════════════════════
    bot_y = spark_y + spark_h + 12
    bot_h = h - bot_y - 10

    # Top blocked — left 60%
    bl_w = int((w - 60) * 0.60)
    draw.text((spark_x, bot_y), "TOP BLOCKED", fill=BLOCK_RED, font=font(16))
    top_blocked = d.get('top_blocked', [])
    bx = spark_x
    by = bot_y + 22
    col_w = bl_w // 3  # 3 columns of blocked domains
    for i, item in enumerate(top_blocked[:12]):
        if isinstance(item, dict):
            domain = list(item.keys())[0]
            count = list(item.values())[0]
        else:
            domain = str(item); count = 0
        col = i // 4
        row = i % 4
        dx = bx + col * col_w
        dy = by + row * 22
        domain_short = domain[:25] if len(domain) > 25 else domain
        draw.text((dx, dy), domain_short, fill=TEXT, font=font(15))
        draw.text((dx + col_w - 50, dy), _fmt(count), fill=BLOCK_RED, font=font(14))

    # Top clients — right 40%
    cl_x = spark_x + bl_w + 30
    cl_w = w - cl_x - 30
    draw.text((cl_x, bot_y), "TOP CLIENTS", fill=PURPLE, font=font(16))
    top_clients = d.get('top_clients', [])
    cy = bot_y + 22
    if top_clients:
        max_c = max(list(c.values())[0] for c in top_clients if isinstance(c, dict))
    else:
        max_c = 1
    for i, item in enumerate(top_clients[:4]):
        if isinstance(item, dict):
            client = list(item.keys())[0]
            count = list(item.values())[0]
        else:
            client = str(item); count = 0
        dy = cy + i * 24
        draw.text((cl_x, dy), client[:18], fill=TEXT_BRIGHT, font=font(16))
        # Mini bar
        bar_pct = count / max_c * 100 if max_c else 0
        bar_w = cl_w - 100
        fill_w = max(0, int(bar_w * bar_pct / 100))
        bar_y = dy + 18
        draw.rectangle([cl_x, bar_y, cl_x + bar_w, bar_y + 4], fill=(15, 18, 28))
        if fill_w > 0:
            c = _lerp_color(ACCENT, PURPLE, i / max(len(top_clients)-1, 1))
            draw.rectangle([cl_x, bar_y, cl_x + fill_w, bar_y + 4], fill=c)
        draw.text((cl_x + bar_w + 8, dy), _fmt(count), fill=TEXT_DIM, font=font(14))

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
        time.sleep(3)
        render_frame().save('/tmp/pihole.png')
        print("Saved to /tmp/pihole.png")
    else:
        print("ADGUARD_PASS=password python3 pihole.py --once")
