"""Internet speed test dashboard for tinyscreen bar display.

Animated particle streams during testing, dramatic arc gauge results.
Real-time speed readout via speedtest-cli callbacks.

Requires: pip install speedtest-cli
"""

import math
import os
import random
import time
import threading
from PIL import Image, ImageDraw, ImageFont

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
DL_COLOR    = (0, 220, 255)
UL_COLOR    = (180, 80, 255)
PING_COLOR  = (0, 255, 160)

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

def _lerp(c1, c2, t):
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
            c = _lerp((6, 10, 18), (2, 4, 8), t)
            draw.line([(0, y), (w, y)], fill=c)
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
    draw.text((x, y), text, fill=color, font=f)

def _draw_arc(draw, img, cx, cy, radius, thickness, pct, color, bg_color=(20, 25, 38)):
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    draw.arc(bbox, 135, 405, fill=bg_color, width=thickness)
    if pct > 0:
        end = 135 + int(270 * min(pct, 100) / 100)
        draw.arc(bbox, 135, end, fill=color, width=thickness)
        angle_rad = math.radians(end)
        tip_x = cx + int(radius * math.cos(angle_rad))
        tip_y = cy + int(radius * math.sin(angle_rad))
        bright = tuple(min(255, c + 80) for c in color)
        draw.ellipse([tip_x-4, tip_y-4, tip_x+4, tip_y+4], fill=bright)

# ── Particle system ────────────────────────────────────────────────
class _Particles:
    def __init__(self, count, w, h):
        self.w = w
        self.h = h
        self.n = count
        self.x = [random.uniform(0, w) for _ in range(count)]
        self.y = [random.uniform(0, h) for _ in range(count)]
        self.speed = [random.uniform(2, 8) for _ in range(count)]
        self.size = [random.uniform(1, 3) for _ in range(count)]
        self.alpha = [random.randint(40, 180) for _ in range(count)]
        self.depth = [random.uniform(0.3, 1.0) for _ in range(count)]

    def update(self, direction, speed_mult):
        for i in range(self.n):
            s = self.speed[i] * speed_mult * self.depth[i]
            if direction == 'right':
                self.x[i] += s
                if self.x[i] > self.w:
                    self.x[i] = 0
                    self.y[i] = random.uniform(0, self.h)
            else:
                self.x[i] -= s
                if self.x[i] < 0:
                    self.x[i] = self.w
                    self.y[i] = random.uniform(0, self.h)

    def draw(self, draw_ctx, color):
        for i in range(self.n):
            d = self.depth[i]
            sz = self.size[i] * d
            a = int(self.alpha[i] * d)
            c = tuple(int(v * d) for v in color) + (a,)
            x, y = int(self.x[i]), int(self.y[i])
            if sz < 1.5:
                draw_ctx.point((x, y), fill=c)
            else:
                r = int(sz)
                draw_ctx.ellipse([x-r, y-r, x+r, y+r], fill=c)

_particles = None

# ── State ──────────────────────────────────────────────────────────
_cache = {
    'download': 0, 'upload': 0, 'ping': 0,
    'server': '', 'isp': '',
    'dl_history': [], 'ul_history': [], 'ping_history': [],
    'last_test': 0, 'testing': False,
    'phase': '', 'live_speed': 0,
    'error': None, 'test_count': 0,
}

TEST_INTERVAL = 15 * 60

def _test_callback(current, total, **kwargs):
    """Callback just to track that we're actively testing."""
    _cache['live_speed'] = -1  # signals "active"

def _do_test():
    try:
        import speedtest as st_lib
        _cache['phase'] = 'server'
        _cache['live_speed'] = 0

        s = st_lib.Speedtest()
        s.get_best_server()
        _cache['server'] = f"{s.best['sponsor']} ({s.best['name']})"
        _cache['ping'] = s.best['latency']

        _cache['phase'] = 'download'
        _cache['live_speed'] = 0
        s.download(callback=_test_callback)
        _cache['download'] = s.results.download / 1_000_000

        _cache['phase'] = 'upload'
        _cache['live_speed'] = 0
        s.upload(callback=_test_callback)
        _cache['upload'] = s.results.upload / 1_000_000

        _cache['isp'] = s.results.client.get('isp', '')
        _cache['dl_history'].append(_cache['download'])
        _cache['ul_history'].append(_cache['upload'])
        _cache['ping_history'].append(_cache['ping'])
        for k in ('dl_history', 'ul_history', 'ping_history'):
            if len(_cache[k]) > 24:
                _cache[k] = _cache[k][-24:]

        _cache['phase'] = 'done'
        _cache['test_count'] += 1
        _cache['error'] = None
    except ImportError:
        _cache['error'] = 'pip install speedtest-cli'
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
    global _particles
    _run_test()

    if _particles is None:
        _particles = _Particles(200, w, h)

    img = _get_bg(w, h)
    draw = ImageDraw.Draw(img)

    phase = _cache['phase']
    testing = _cache['testing'] or (_cache['test_count'] == 0 and not _cache['error'])

    # ══════════════════════════════════════════════════════════════
    # TESTING STATE — animated particles + live speed
    # ══════════════════════════════════════════════════════════════
    if testing:
        # Determine particle direction and color
        if phase == 'download':
            direction = 'right'
            color = DL_COLOR
            label = "DOWNLOADING"
            speed_mult = max(2, _cache['live_speed'] / 5)
        elif phase == 'upload':
            direction = 'left'
            color = UL_COLOR
            label = "UPLOADING"
            speed_mult = max(2, _cache['live_speed'] / 5)
        else:
            direction = 'right'
            color = ACCENT
            label = "FINDING SERVER" if phase == 'server' else "CONNECTING"
            speed_mult = 3

        # Update and draw particles
        _particles.update(direction, speed_mult)
        _particles.draw(draw, color)

        # Phase label — large, centered top
        f = font(36)
        tw = f.getlength(label)
        _hero(draw, img, int((w - tw) / 2), 20, label, color, 36)
        draw = ImageDraw.Draw(img)

        # Animated dots
        dots = '·' * (int(time.time() * 3) % 4 + 1)
        draw.text((int((w + tw) / 2) + 20, 28), dots, fill=color, font=font(30))

        # Pulsing speed indicator in center
        pulse = 0.7 + 0.3 * math.sin(time.time() * 4)
        pulse_color = tuple(int(c * pulse) for c in color)
        if phase in ('download', 'upload'):
            # Animated measuring bars
            t = time.time()
            for bar_i in range(8):
                bar_x = w // 2 - 200 + bar_i * 55
                bar_max_h = 120
                bar_h2 = int(bar_max_h * (0.3 + 0.7 * abs(math.sin(t * 3 + bar_i * 0.8))))
                bar_y2 = h // 2 + 40 - bar_h2
                bar_c = _lerp(color, tuple(min(255, c + 60) for c in color),
                              abs(math.sin(t * 3 + bar_i * 0.8)))
                draw.rectangle([bar_x, bar_y2, bar_x + 35, h // 2 + 40],
                              fill=tuple(max(0, c - 80) for c in bar_c))
                draw.rectangle([bar_x, bar_y2, bar_x + 35, bar_y2 + 3], fill=bar_c)
        else:
            # Server search — spinning dots
            cx, cy = w // 2, h // 2
            t = time.time()
            for dot_i in range(8):
                angle = t * 2 + dot_i * math.pi / 4
                dx = cx + int(60 * math.cos(angle))
                dy = cy + int(60 * math.sin(angle))
                a = int(255 * (0.3 + 0.7 * ((dot_i / 8 + t * 0.5) % 1.0)))
                dot_c = color + (a,)
                draw.ellipse([dx-4, dy-4, dx+4, dy+4], fill=dot_c)

        # Already-measured values at bottom
        by = h - 60
        if _cache['ping'] > 0:
            draw.text((40, by), "PING", fill=PING_COLOR, font=font(16))
            draw.text((40, by + 20), f"{_cache['ping']:.0f}ms", fill=PING_COLOR, font=font(28))
        if _cache['download'] > 0 and phase != 'download':
            draw.text((300, by), "DOWNLOAD", fill=DL_COLOR, font=font(16))
            draw.text((300, by + 20), f"{_cache['download']:.1f} Mbps", fill=DL_COLOR, font=font(28))
        if _cache['server']:
            draw.text((w - 500, by + 10), _cache['server'], fill=TEXT_DIM, font=font(16))

        img = Image.alpha_composite(img, _get_scanlines(w, h))
        out = Image.new('RGB', (w, h), BG)
        out.paste(img, (0, 0), img)
        return out

    # ══════════════════════════════════════════════════════════════
    # ERROR
    # ══════════════════════════════════════════════════════════════
    if _cache['error'] and _cache['test_count'] == 0:
        draw.text((30, 20), "SPEED TEST", fill=ACCENT, font=font(30))
        draw.text((30, 70), f"Error: {_cache['error']}", fill=RED, font=font(22))
        img = Image.alpha_composite(img, _get_scanlines(w, h))
        out = Image.new('RGB', (w, h), BG)
        out.paste(img, (0, 0), img)
        return out

    # ══════════════════════════════════════════════════════════════
    # RESULTS — dramatic arc gauges + history
    # ══════════════════════════════════════════════════════════════
    dl = _cache['download']
    ul = _cache['upload']
    ping = _cache['ping']

    # Gentle ambient particles (slow, dim)
    _particles.update('right', 1.5)
    for i in range(_particles.n):
        _particles.alpha[i] = min(_particles.alpha[i], 40)
    _particles.draw(draw, (20, 40, 60))
    for i in range(_particles.n):
        _particles.alpha[i] = random.randint(40, 180)

    # ── Download arc gauge (left) ──
    max_speed = 200  # scale: 0-200 Mbps
    dl_pct = min(dl / max_speed * 100, 100)
    gauge_r = 130
    gauge_cx = 220
    gauge_cy = h // 2 + 20

    _draw_arc(draw, img, gauge_cx, gauge_cy, gauge_r, 14, dl_pct, DL_COLOR)
    draw = ImageDraw.Draw(img)

    # Speed inside arc
    _hero(draw, img, gauge_cx - 80, gauge_cy - 50, f"{dl:.1f}", DL_COLOR, 64)
    draw = ImageDraw.Draw(img)
    draw.text((gauge_cx - 28, gauge_cy + 20), "Mbps", fill=DL_COLOR, font=font(20))
    draw.text((gauge_cx - 48, gauge_cy - 85), "DOWNLOAD", fill=TEXT, font=font(18))

    # ── Upload arc gauge (center-left) ──
    ul_pct = min(ul / max_speed * 100, 100)
    gauge2_cx = 560
    gauge2_r = 100

    _draw_arc(draw, img, gauge2_cx, gauge_cy + 10, gauge2_r, 12, ul_pct, UL_COLOR)
    draw = ImageDraw.Draw(img)

    _hero(draw, img, gauge2_cx - 60, gauge_cy - 30, f"{ul:.1f}", UL_COLOR, 48)
    draw = ImageDraw.Draw(img)
    draw.text((gauge2_cx - 22, gauge_cy + 20), "Mbps", fill=UL_COLOR, font=font(18))
    draw.text((gauge2_cx - 35, gauge_cy - 65), "UPLOAD", fill=TEXT, font=font(18))

    # ── Ping + Rating (center-right) ──
    px = 780
    draw.text((px, 30), "PING", fill=TEXT, font=font(20))
    ping_color = GREEN if ping < 30 else YELLOW if ping < 80 else RED
    _hero(draw, img, px, 56, f"{ping:.0f}", ping_color, 60)
    draw = ImageDraw.Draw(img)
    draw.text((px + font(60).getlength(f"{ping:.0f}") + 8, 82), "ms",
              fill=ping_color, font=font(22))

    # Rating
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

    draw.text((px, 140), "RATING", fill=TEXT, font=font(20))
    _hero(draw, img, px, 168, rating, r_color, 48)
    draw = ImageDraw.Draw(img)

    # Server info
    draw.text((px, 230), "SERVER", fill=TEXT_DIM, font=font(14))
    draw.text((px, 248), _cache['server'][:35], fill=TEXT, font=font(16))
    if _cache['isp']:
        draw.text((px, 272), _cache['isp'][:35], fill=TEXT_DIM, font=font(14))

    # Next test
    next_s = max(0, TEST_INTERVAL - (time.time() - _cache['last_test']))
    draw.text((px, 310), "NEXT TEST", fill=TEXT_DIM, font=font(14))
    draw.text((px, 328), f"{int(next_s // 60)}m {int(next_s % 60)}s", fill=TEXT, font=font(20))

    # ── History sparklines (right side) ──
    sp_x = 1050
    sp_w = w - sp_x - 30

    # Download history
    draw.text((sp_x, 20), "DOWNLOAD HISTORY", fill=DL_COLOR, font=font(16))
    if _cache['dl_history']:
        avg = sum(_cache['dl_history']) / len(_cache['dl_history'])
        draw.text((sp_x + 250, 20), f"avg {avg:.1f}", fill=TEXT_DIM, font=font(14))

    sp_y = 40
    sp_h = 100
    dl_data = _cache['dl_history']
    if dl_data and len(dl_data) >= 2:
        draw.rectangle([sp_x, sp_y, sp_x + sp_w, sp_y + sp_h], fill=(6, 8, 14))
        mx = max(dl_data) or 1
        pts = []
        for i, v in enumerate(dl_data):
            px2 = sp_x + int(i * sp_w / (len(dl_data) - 1))
            py2 = sp_y + sp_h - int(min(v, mx) * sp_h / mx)
            pts.append((px2, py2))
        fill_pts = pts + [(sp_x + sp_w, sp_y + sp_h), (sp_x, sp_y + sp_h)]
        draw.polygon(fill_pts, fill=(0, 30, 50))
        draw.line(pts, fill=DL_COLOR, width=2)
    else:
        draw.rectangle([sp_x, sp_y, sp_x + sp_w, sp_y + sp_h], fill=(6, 8, 14))
        draw.text((sp_x + sp_w//2 - 50, sp_y + sp_h//2 - 8), "1 test", fill=TEXT_DIM, font=font(16))

    # Upload history
    sp_y2 = sp_y + sp_h + 20
    draw.text((sp_x, sp_y2 - 16), "UPLOAD HISTORY", fill=UL_COLOR, font=font(16))
    ul_data = _cache['ul_history']
    if ul_data and len(ul_data) >= 2:
        draw.rectangle([sp_x, sp_y2, sp_x + sp_w, sp_y2 + sp_h], fill=(6, 8, 14))
        mx = max(ul_data) or 1
        pts = []
        for i, v in enumerate(ul_data):
            px2 = sp_x + int(i * sp_w / (len(ul_data) - 1))
            py2 = sp_y2 + sp_h - int(min(v, mx) * sp_h / mx)
            pts.append((px2, py2))
        fill_pts = pts + [(sp_x + sp_w, sp_y2 + sp_h), (sp_x, sp_y2 + sp_h)]
        draw.polygon(fill_pts, fill=(25, 10, 45))
        draw.line(pts, fill=UL_COLOR, width=2)
    else:
        draw.rectangle([sp_x, sp_y2, sp_x + sp_w, sp_y2 + sp_h], fill=(6, 8, 14))
        draw.text((sp_x + sp_w//2 - 50, sp_y2 + sp_h//2 - 8), "1 test", fill=TEXT_DIM, font=font(16))

    # Ping sparkline (thin, bottom right)
    sp_y3 = sp_y2 + sp_h + 20
    draw.text((sp_x, sp_y3 - 14), "PING", fill=PING_COLOR, font=font(14))
    ping_data = _cache['ping_history']
    if ping_data and len(ping_data) >= 2:
        ph2 = 30
        draw.rectangle([sp_x, sp_y3, sp_x + sp_w, sp_y3 + ph2], fill=(6, 8, 14))
        mx = max(ping_data) or 1
        pts = [(sp_x + int(i * sp_w / (len(ping_data)-1)),
                sp_y3 + ph2 - int(min(v, mx) * ph2 / mx))
               for i, v in enumerate(ping_data)]
        draw.line(pts, fill=PING_COLOR, width=2)

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
        while _cache['testing']:
            time.sleep(1)
            print(f"  {_cache['phase']} {_cache['live_speed']:.1f} Mbps")
        render_frame().save('/tmp/speedtest.png')
        print("Saved to /tmp/speedtest.png")
    else:
        print("Usage: python3 speedtest_mode.py --once")
