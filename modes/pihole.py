"""DNS ad-blocker dashboard for tinyscreen bar display.

Supports AdGuard Home and Pi-hole. Full-width visual dashboard with
arc gauge, glowing sparklines, and animated block counter.

Configuration via environment variables:
  ADGUARD_URL=http://192.168.1.50    ADGUARD_USER=admin  ADGUARD_PASS=password
  -- or --
  PIHOLE_URL=http://192.168.1.50     PIHOLE_API_KEY=abc123
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
SHIELD_GREEN = (0, 200, 100)
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

# ── Visual utilities ───────────────────────────────────────────────
def _lerp_color(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

_bg_cache = {}
def _get_bg(w, h):
    if (w, h) not in _bg_cache:
        img = Image.new('RGBA', (w, h), BG + (255,))
        draw = ImageDraw.Draw(img)
        for y in range(h):
            t = y / max(h - 1, 1)
            c = _lerp_color((8, 12, 22), (4, 6, 12), t)
            draw.line([(0, y), (w, y)], fill=c)
        grid_c = (18, 24, 38, 50)
        for gx in range(0, w, 40):
            draw.line([(gx, 0), (gx, h)], fill=grid_c)
        for gy in range(0, h, 40):
            draw.line([(0, gy), (w, gy)], fill=grid_c)
        for gx in range(0, w, 80):
            for gy in range(0, h, 80):
                draw.ellipse([gx-2, gy-2, gx+2, gy+2], fill=ACCENT+(25,))
                draw.ellipse([gx-1, gy-1, gx+1, gy+1], fill=ACCENT+(45,))
        for i in range(50):
            a = int(30 * (1.0 - i / 50))
            draw.line([(0, h-1-i), (w, h-1-i)], fill=(0, 100, 180, a))
        vig = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        vd = ImageDraw.Draw(vig)
        for i in range(40):
            a = int(50 * (1.0 - i / 40))
            vd.line([(0, i), (w, i)], fill=(0, 0, 0, a))
            vd.line([(0, h-1-i), (w, h-1-i)], fill=(0, 0, 0, a))
        for i in range(60):
            a = int(40 * (1.0 - i / 60))
            vd.line([(i, 0), (i, h)], fill=(0, 0, 0, a))
            vd.line([(w-1-i, 0), (w-1-i, h)], fill=(0, 0, 0, a))
        img = Image.alpha_composite(img, vig)
        _bg_cache[(w, h)] = img
    return _bg_cache[(w, h)].copy()

_scanline_cache = {}
def _get_scanlines(w, h):
    key = (w, h)
    if key not in _scanline_cache:
        sl = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sl)
        for y in range(0, h, 3):
            sd.line([(0, y), (w, y)], fill=(0, 0, 0, 55))
        _scanline_cache[key] = sl
    return _scanline_cache[key]

def _draw_panel(draw, img, x, y, w, h, accent_color=ACCENT):
    top_c = (14, 18, 30)
    bot_c = (8, 11, 20)
    for row in range(h):
        t = row / max(h - 1, 1)
        c = _lerp_color(top_c, bot_c, t)
        draw.line([(x, y + row), (x + w, y + row)], fill=c)
    glow = Image.new('RGBA', (w, 18), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rectangle([0, 0, w, 2], fill=accent_color + (220,))
    gd.rectangle([0, 2, w, 6], fill=accent_color + (50,))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=4))
    img.paste(glow, (x, y - 2), glow)

def _draw_hero(draw, img, x, y, text, color, size):
    f = font(size)
    bbox = f.getbbox(text)
    tw = bbox[2] - bbox[0] + 20
    th = bbox[3] - bbox[1] + 20
    pad = 10
    glow = Image.new('RGBA', (tw + pad*2, th + pad*2), (0,0,0,0))
    gd = ImageDraw.Draw(glow)
    gd.text((pad - bbox[0], pad - bbox[1]), text, fill=color+(100,), font=f)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=6))
    img.paste(glow, (x - pad, y - pad), glow)
    draw.text((x, y), text, fill=color, font=f)

def _draw_arc_gauge(draw, img, cx, cy, radius, thickness, pct, color):
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    draw.arc(bbox, 200, 340, fill=(25, 30, 45), width=thickness)
    if pct > 0:
        end_angle = 200 + int(140 * min(pct, 100) / 100)
        gpad = 25
        gsize = radius * 2 + gpad * 2
        glow = Image.new('RGBA', (gsize, gsize), (0,0,0,0))
        gd = ImageDraw.Draw(glow)
        gbbox = [gpad, gpad, gpad + radius*2, gpad + radius*2]
        gd.arc(gbbox, 200, end_angle, fill=color+(80,), width=thickness+14)
        glow = glow.filter(ImageFilter.GaussianBlur(radius=10))
        img.paste(glow, (cx-radius-gpad, cy-radius-gpad), glow)
        glow2 = Image.new('RGBA', (gsize, gsize), (0,0,0,0))
        gd2 = ImageDraw.Draw(glow2)
        gd2.arc(gbbox, 200, end_angle, fill=color+(160,), width=thickness+4)
        glow2 = glow2.filter(ImageFilter.GaussianBlur(radius=4))
        img.paste(glow2, (cx-radius-gpad, cy-radius-gpad), glow2)
        draw.arc(bbox, 200, end_angle, fill=color, width=thickness)

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

def _draw_sparkline(draw, img, x, y, w, h, data, color, filled=True):
    if not data or len(data) < 2:
        draw.rectangle([x, y, x + w, y + h], fill=(10, 13, 20))
        return
    draw.rectangle([x, y, x + w, y + h], fill=(10, 13, 20))
    mx = max(data) or 1
    points = []
    for i, val in enumerate(data):
        px = x + int(i * w / (len(data) - 1))
        py = y + h - int(min(val, mx) * h / mx)
        points.append((px, py))
    if filled:
        dim_fill = tuple(max(0, c - 170) for c in color)
        fill_pts = points + [(x + w, y + h), (x, y + h)]
        draw.polygon(fill_pts, fill=dim_fill)
    pad = 8
    gw, gh = w + pad*2, h + pad*2
    glow = Image.new('RGBA', (gw, gh), (0,0,0,0))
    gd = ImageDraw.Draw(glow)
    shifted = [(px - x + pad, py - y + pad) for px, py in points]
    gd.line(shifted, fill=color+(140,), width=5)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=6))
    img.paste(glow, (x - pad, y - pad), glow)
    draw.line(points, fill=tuple(min(255, c + 40) for c in color), width=2)

# ── API backends ───────────────────────────────────────────────────
def _fetch_adguard(url, user, passwd):
    import requests
    auth = (user, passwd) if user else None
    data = {}
    try:
        r = requests.get(f'{url}/control/status', auth=auth, timeout=5)
        r.raise_for_status()
        status = r.json()
        data['running'] = status.get('running', False)
        data['protection'] = status.get('protection_enabled', False)
        data['version'] = status.get('version', '?')
        r = requests.get(f'{url}/control/stats', auth=auth, timeout=5)
        r.raise_for_status()
        stats = r.json()
        data['total_queries'] = stats.get('num_dns_queries', 0)
        data['blocked'] = stats.get('num_blocked_filtering', 0)
        data['blocked_pct'] = (data['blocked'] / data['total_queries'] * 100
                               if data['total_queries'] > 0 else 0)
        data['avg_ms'] = stats.get('avg_processing_time', 0) * 1000
        data['top_blocked'] = stats.get('top_blocked_domains', [])[:10]
        data['top_clients'] = stats.get('top_clients', [])[:8]
        data['top_queried'] = stats.get('top_queried_domains', [])[:5]
        data['queries_over_time'] = stats.get('dns_queries', [])
        data['blocked_over_time'] = stats.get('blocked_filtering', [])
        data['backend'] = 'AdGuard'
        data['error'] = None
    except Exception as e:
        data['error'] = str(e)[:80]
    return data

def _fetch_pihole(url, api_key):
    import requests
    data = {}
    try:
        params = {'auth': api_key} if api_key else {}
        r = requests.get(f'{url}/admin/api.php?summary', params=params, timeout=5)
        r.raise_for_status()
        s = r.json()
        data['total_queries'] = int(s.get('dns_queries_today', 0))
        data['blocked'] = int(s.get('ads_blocked_today', 0))
        data['blocked_pct'] = float(s.get('ads_percentage_today', 0))
        data['protection'] = s.get('status') == 'enabled'
        data['running'] = True
        data['version'] = ''
        data['avg_ms'] = 0
        data['top_blocked'] = []
        data['top_clients'] = []
        data['top_queried'] = []
        data['queries_over_time'] = []
        data['blocked_over_time'] = []
        r2 = requests.get(f'{url}/admin/api.php?topItems=10', params=params, timeout=5)
        if r2.status_code == 200:
            top = r2.json()
            data['top_blocked'] = [{d: c} for d, c in list(top.get('top_ads', {}).items())[:10]]
        r3 = requests.get(f'{url}/admin/api.php?overTimeData10mins', params=params, timeout=5)
        if r3.status_code == 200:
            ot = r3.json()
            data['queries_over_time'] = list(ot.get('domains_over_time', {}).values())[-24:]
            data['blocked_over_time'] = list(ot.get('ads_over_time', {}).values())[-24:]
        data['backend'] = 'Pi-hole'
        data['error'] = None
    except Exception as e:
        data['error'] = str(e)[:80]
    return data

# ── State ──────────────────────────────────────────────────────────
_cache = {'data': {}, 'last_fetch': 0, 'fetching': False, 'error': None}
FETCH_INTERVAL = 10

def _detect_backend():
    ag_url = os.environ.get('ADGUARD_URL', 'http://192.168.1.50')
    ag_user = os.environ.get('ADGUARD_USER', 'admin')
    ag_pass = os.environ.get('ADGUARD_PASS', '')
    ph_url = os.environ.get('PIHOLE_URL', '')
    ph_key = os.environ.get('PIHOLE_API_KEY', '')
    if ph_url:
        return 'pihole', ph_url, ph_key, '', ''
    return 'adguard', ag_url, '', ag_user, ag_pass

def _do_fetch():
    backend, url, api_key, user, passwd = _detect_backend()
    try:
        if backend == 'pihole':
            data = _fetch_pihole(url, api_key)
        else:
            data = _fetch_adguard(url, user, passwd)
        _cache['data'] = data
        _cache['error'] = data.get('error')
    except Exception as e:
        _cache['error'] = str(e)[:80]
    finally:
        _cache['fetching'] = False
        _cache['last_fetch'] = time.time()

def _fetch():
    now = time.time()
    if now - _cache['last_fetch'] < FETCH_INTERVAL or _cache['fetching']:
        return
    _cache['fetching'] = True
    threading.Thread(target=_do_fetch, daemon=True).start()

def init():
    _cache['last_fetch'] = 0
    _cache['data'] = {}
    _fetch()

# ── Helpers ────────────────────────────────────────────────────────
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
    pad = 8
    gap = 8

    # ── Auth/error state ──
    if not d or (_cache['error'] and not d.get('total_queries')):
        err = _cache['error'] or 'Connecting...'
        draw.text((pad + 20, 20), "DNS BLOCKER", fill=ACCENT, font=font(28))
        if '401' in str(err) or 'Unauthorized' in str(err):
            draw.text((pad + 20, 70), "Authentication required", fill=YELLOW, font=font(24))
            draw.text((pad + 20, 115), "ADGUARD_PASS=password tinyscreen --pihole", fill=CYAN, font=font(20))
            draw.text((pad + 20, 155), "Or for Pi-hole:", fill=TEXT_DIM, font=font(18))
            draw.text((pad + 20, 185), "PIHOLE_URL=http://ip PIHOLE_API_KEY=key tinyscreen --pihole", fill=CYAN, font=font(18))
        elif _cache['fetching']:
            dots = '.' * (int(time.time() * 2) % 4)
            draw.text((w//2 - 100, h//2 - 12), f"Connecting{dots}", fill=ACCENT, font=font(28))
        else:
            draw.text((pad + 20, 70), f"Error: {err}", fill=RED, font=font(20))
        img = Image.alpha_composite(img, _get_scanlines(w, h))
        out = Image.new('RGB', (w, h), BG)
        out.paste(img, (0, 0), img)
        return out

    content_y = pad
    content_h = h - pad * 2
    protected = d.get('protection', False)
    total = d.get('total_queries', 0)
    blocked = d.get('blocked', 0)
    blocked_pct = d.get('blocked_pct', 0)
    avg_ms = d.get('avg_ms', 0)

    # ═══════════════════════════════════════════════════════════════
    # Panel 1: Shield + Block Rate Arc Gauge (18%)
    # ═══════════════════════════════════════════════════════════════
    p1w = int(w * 0.18)
    _draw_panel(draw, img, pad, content_y, p1w, content_h,
                accent_color=SHIELD_GREEN if protected else RED)
    draw = ImageDraw.Draw(img)

    # Protection status
    status_text = "PROTECTED" if protected else "DISABLED"
    status_color = SHIELD_GREEN if protected else RED
    draw.text((pad + 16, content_y + 10), status_text, fill=status_color, font=font(22))

    # Arc gauge for block rate
    gauge_r = 65
    gauge_cx = pad + p1w // 2
    gauge_cy = content_y + 90 + gauge_r
    _draw_arc_gauge(draw, img, gauge_cx, gauge_cy, gauge_r, 10, blocked_pct,
                    RED if blocked_pct > 20 else ORANGE if blocked_pct > 10 else GREEN)
    draw = ImageDraw.Draw(img)

    # Block percentage inside arc
    _draw_hero(draw, img, gauge_cx - 40, gauge_cy - 25,
               f"{blocked_pct:.1f}%", BLOCK_RED, 34)
    draw = ImageDraw.Draw(img)
    draw.text((gauge_cx - 28, gauge_cy + 10), "BLOCKED", fill=TEXT_DIM, font=font(14))

    # Avg response time at bottom
    if avg_ms > 0:
        ms_color = GREEN if avg_ms < 50 else YELLOW if avg_ms < 100 else RED
        draw.text((pad + 16, content_y + content_h - 50), "RESPONSE", fill=TEXT_DIM, font=font(14))
        draw.text((pad + 16, content_y + content_h - 30), f"{avg_ms:.1f}ms",
                  fill=ms_color, font=font(24))

    # ═══════════════════════════════════════════════════════════════
    # Panel 2: Hero Stats + Timeline (40%)
    # ═══════════════════════════════════════════════════════════════
    p2x = pad + p1w + gap
    p2w = int(w * 0.40)
    _draw_panel(draw, img, p2x, content_y, p2w, content_h, accent_color=ACCENT)
    draw = ImageDraw.Draw(img)

    backend = d.get('backend', 'DNS')
    draw.text((p2x + 16, content_y + 8), f"{backend} Home", fill=ACCENT, font=font(18))

    # Hero stats row
    sy = content_y + 34
    # Total queries
    draw.text((p2x + 16, sy), "QUERIES", fill=TEXT_DIM, font=font(14))
    _draw_hero(draw, img, p2x + 16, sy + 18, _fmt(total), ACCENT, 42)
    draw = ImageDraw.Draw(img)

    # Blocked count
    draw.text((p2x + 220, sy), "BLOCKED", fill=TEXT_DIM, font=font(14))
    _draw_hero(draw, img, p2x + 220, sy + 18, _fmt(blocked), BLOCK_RED, 42)
    draw = ImageDraw.Draw(img)

    # Passed
    passed = total - blocked
    draw.text((p2x + 420, sy), "ALLOWED", fill=TEXT_DIM, font=font(14))
    _draw_hero(draw, img, p2x + 420, sy + 18, _fmt(passed), GREEN, 42)
    draw = ImageDraw.Draw(img)

    # Queries timeline (full width of panel)
    queries_ot = d.get('queries_over_time', [])
    blocked_ot = d.get('blocked_over_time', [])

    spark_y = sy + 80
    spark_h = 80
    spark_w = p2w - 32

    # Draw queries sparkline
    _draw_sparkline(draw, img, p2x + 16, spark_y, spark_w, spark_h,
                    queries_ot, ACCENT)
    draw = ImageDraw.Draw(img)

    # Overlay blocked on same chart (different color, no fill)
    if blocked_ot and len(blocked_ot) >= 2:
        mx = max(queries_ot) if queries_ot else 1
        points = []
        for i, val in enumerate(blocked_ot):
            px = p2x + 16 + int(i * spark_w / (len(blocked_ot) - 1))
            py = spark_y + spark_h - int(min(val, mx) * spark_h / mx)
            points.append((px, py))
        draw.line(points, fill=BLOCK_RED, width=2)

    draw.text((p2x + 16, spark_y + spark_h + 4), "24h  —", fill=TEXT_DIM, font=font(13))
    draw.text((p2x + 70, spark_y + spark_h + 4), "queries", fill=ACCENT, font=font(13))
    draw.text((p2x + 140, spark_y + spark_h + 4), "blocked", fill=BLOCK_RED, font=font(13))

    # Block rate bar across bottom of panel
    bar_y = spark_y + spark_h + 26
    draw.text((p2x + 16, bar_y), "BLOCK RATE", fill=TEXT_DIM, font=font(14))
    _draw_glow_bar(draw, img, p2x + 130, bar_y + 2, p2w - 160, 14, blocked_pct,
                   RED if blocked_pct > 20 else ORANGE if blocked_pct > 10 else GREEN)
    draw = ImageDraw.Draw(img)

    # ═══════════════════════════════════════════════════════════════
    # Panel 3: Top Blocked Domains (22%)
    # ═══════════════════════════════════════════════════════════════
    p3x = p2x + p2w + gap
    p3w = int(w * 0.22)
    _draw_panel(draw, img, p3x, content_y, p3w, content_h, accent_color=BLOCK_RED)
    draw = ImageDraw.Draw(img)

    draw.text((p3x + 16, content_y + 8), "TOP BLOCKED", fill=BLOCK_RED, font=font(18))

    top_blocked = d.get('top_blocked', [])
    ty = content_y + 34
    row_h = min(26, (content_h - 50) // max(len(top_blocked), 1))
    for i, item in enumerate(top_blocked[:14]):
        if isinstance(item, dict):
            domain = list(item.keys())[0]
            count = list(item.values())[0]
        else:
            domain = str(item)
            count = 0
        ry = ty + i * row_h
        if ry + row_h > content_y + content_h - 5:
            break
        # Truncate domain
        domain_short = domain[:28] if len(domain) > 28 else domain
        # Alternating subtle background
        if i % 2 == 0:
            draw.rectangle([p3x + 2, ry - 1, p3x + p3w - 2, ry + row_h - 3],
                          fill=(12, 16, 26))
        draw.text((p3x + 16, ry), domain_short, fill=TEXT, font=font(15))
        if count:
            count_str = _fmt(count)
            draw.text((p3x + p3w - 55, ry), count_str, fill=TEXT_DIM, font=font(15))

    # ═══════════════════════════════════════════════════════════════
    # Panel 4: Top Clients (20%)
    # ═══════════════════════════════════════════════════════════════
    p4x = p3x + p3w + gap
    p4w = w - p4x - pad
    _draw_panel(draw, img, p4x, content_y, p4w, content_h, accent_color=PURPLE)
    draw = ImageDraw.Draw(img)

    draw.text((p4x + 16, content_y + 8), "TOP CLIENTS", fill=PURPLE, font=font(18))

    top_clients = d.get('top_clients', [])
    ty = content_y + 34
    if top_clients:
        max_queries = max(list(c.values())[0] for c in top_clients if isinstance(c, dict))
    else:
        max_queries = 1
    row_h = min(32, (content_h - 50) // max(len(top_clients), 1))

    for i, item in enumerate(top_clients[:10]):
        if isinstance(item, dict):
            client = list(item.keys())[0]
            count = list(item.values())[0]
        else:
            client = str(item)
            count = 0
        ry = ty + i * row_h
        if ry + row_h > content_y + content_h - 5:
            break
        # Client IP/name
        client_short = client[:20] if len(client) > 20 else client
        draw.text((p4x + 16, ry), client_short, fill=TEXT_BRIGHT, font=font(15))

        # Mini bar showing relative query volume
        bar_pct = (count / max_queries * 100) if max_queries > 0 else 0
        bar_w = p4w - 32
        bar_y_pos = ry + 18
        fill_w = max(0, int(bar_w * bar_pct / 100))
        draw.rectangle([p4x + 16, bar_y_pos, p4x + 16 + bar_w, bar_y_pos + 6],
                      fill=(12, 15, 22))
        if fill_w > 0:
            bar_color = _lerp_color(ACCENT, PURPLE, i / max(len(top_clients) - 1, 1))
            draw.rectangle([p4x + 16, bar_y_pos, p4x + 16 + fill_w, bar_y_pos + 6],
                          fill=bar_color)
        draw.text((p4x + p4w - 50, ry), _fmt(count), fill=TEXT_DIM, font=font(14))

    # ── Bottom accent ──
    for i in range(8):
        a = int(180 * (1.0 - i / 8))
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
        img = render_frame()
        img.save('/tmp/pihole.png')
        print("Saved to /tmp/pihole.png")
    else:
        print("Usage: python3 pihole.py --once")
        print("Set ADGUARD_PASS=password or PIHOLE_API_KEY=key")
