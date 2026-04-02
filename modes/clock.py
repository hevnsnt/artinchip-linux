#!/usr/bin/env python3
"""
tinyscreen clock — beautiful clock + weather + system info dashboard.

Left 40%:  massive digital clock with date
Middle 30%: weather from wttr.in
Right 30%:  system quick stats

Designed for 1920x440 stretched bar LCDs.
"""

import os
import time
import subprocess
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

# ── Weather state ───────────────────────────────────────────────────
_weather = None
_weather_last_fetch = 0.0
_weather_error = None
WEATHER_INTERVAL = 300  # 5 minutes

import math
import random

# Animated weather icon state
_anim_frame = 0
_rain_drops = []     # [(x, y, speed, length)]
_snow_flakes = []    # [(x, y, speed, drift)]
_grass_blades = []   # [(x, height, phase)]
_lightning_timer = 0
_lightning_on = False

def _init_rain(ix, iy, iw, ih, count=25):
    global _rain_drops
    _rain_drops = [(random.randint(ix, ix+iw), random.randint(iy, iy+ih),
                    random.randint(4, 8), random.randint(8, 20)) for _ in range(count)]

def _init_snow(ix, iy, iw, ih, count=20):
    global _snow_flakes
    _snow_flakes = [(random.randint(ix, ix+iw), random.randint(iy, iy+ih),
                    random.uniform(1, 3), random.uniform(-1, 1)) for _ in range(count)]

def _init_grass(ix, iy, iw, ih, count=30):
    global _grass_blades
    _grass_blades = [(ix + i * (iw // count), random.randint(8, 18),
                     random.uniform(0, math.pi*2)) for i in range(count)]

def _draw_animated_icon(draw, ix, iy, iw, ih, condition, temp_f):
    """Draw an animated weather scene in the given rectangle."""
    global _anim_frame, _rain_drops, _snow_flakes, _grass_blades
    global _lightning_timer, _lightning_on
    _anim_frame += 1
    t = _anim_frame * 0.1  # time variable for smooth animation

    cond = condition.lower()
    is_hot = False
    try:
        is_hot = temp_f and int(temp_f) >= 95
    except (ValueError, TypeError):
        pass

    cx, cy = ix + iw // 2, iy + ih // 2  # center

    if 'thunder' in cond or 'storm' in cond:
        # ── Thunderstorm: dark cloud + rain + lightning flashes ──
        _draw_cloud(draw, cx - 40, iy + 10, 80, 35, (60, 60, 80))
        # Lightning
        _lightning_timer -= 1
        if _lightning_timer <= 0:
            _lightning_on = random.random() < 0.15
            _lightning_timer = 2 if _lightning_on else random.randint(5, 20)
        if _lightning_on:
            # Flash background
            draw.rectangle([ix, iy, ix+iw, iy+ih], fill=(60, 60, 90))
            _draw_cloud(draw, cx - 40, iy + 10, 80, 35, (120, 120, 150))
            # Lightning bolt
            bx = cx + random.randint(-20, 20)
            pts = [(bx, iy+45), (bx-8, iy+65), (bx+5, iy+63), (bx-12, iy+90)]
            draw.line(pts, fill=(255, 255, 200), width=2)
        # Rain
        if not _rain_drops:
            _init_rain(ix, iy + 45, iw, ih - 45)
        _animate_rain(draw, ix, iy + 45, iw, ih - 45, (100, 140, 255))

    elif 'snow' in cond or 'sleet' in cond:
        # ── Snow: cloud + falling snowflakes ──
        _draw_cloud(draw, cx - 35, iy + 10, 70, 30, (140, 150, 170))
        if not _snow_flakes:
            _init_snow(ix, iy + 40, iw, ih - 40)
        for i in range(len(_snow_flakes)):
            sx, sy, spd, drift = _snow_flakes[i]
            sy += spd
            sx += math.sin(t + drift * 3) * 1.5
            if sy > iy + ih:
                sy = iy + 40
                sx = random.randint(ix, ix + iw)
            _snow_flakes[i] = (sx, sy, spd, drift)
            size = random.choice([2, 3])
            draw.ellipse([sx-size, sy-size, sx+size, sy+size], fill=(220, 230, 255))

    elif 'rain' in cond or 'drizzle' in cond:
        # ── Rain: grey cloud + falling drops ──
        _draw_cloud(draw, cx - 35, iy + 10, 70, 30, (100, 110, 130))
        heavy = 'heavy' in cond
        if not _rain_drops:
            _init_rain(ix, iy + 40, iw, ih - 40, 35 if heavy else 20)
        color = (80, 130, 255) if heavy else (100, 160, 255)
        _animate_rain(draw, ix, iy + 40, iw, ih - 40, color)

    elif 'fog' in cond or 'mist' in cond or 'haze' in cond:
        # ── Fog: horizontal drifting lines ──
        for i in range(8):
            y_line = iy + 15 + i * (ih // 8)
            x_off = math.sin(t * 0.5 + i * 0.7) * 20
            alpha = 80 + int(math.sin(t * 0.3 + i) * 40)
            color = (alpha, alpha + 10, alpha + 20)
            draw.line([(ix + 5 + x_off, y_line), (ix + iw - 5 + x_off, y_line)],
                     fill=color, width=2)

    elif is_hot:
        # ── ANGRY HOT SUN: red furious face ──
        r = min(iw, ih) // 2 - 10
        # Pulsing glow
        pulse = math.sin(t * 3) * 5
        glow_r = r + 8 + int(pulse)
        draw.ellipse([cx-glow_r, cy-glow_r-5, cx+glow_r, cy+glow_r-5], fill=(80, 10, 0))
        # Sun body
        draw.ellipse([cx-r, cy-r-5, cx+r, cy+r-5], fill=(255, 50, 0))
        # Angry eyebrows (angled down toward center)
        eb_y = cy - r//3
        draw.line([(cx-r//2, eb_y-8), (cx-r//5, eb_y+2)], fill=(120, 0, 0), width=3)
        draw.line([(cx+r//2, eb_y-8), (cx+r//5, eb_y+2)], fill=(120, 0, 0), width=3)
        # Angry eyes
        draw.ellipse([cx-r//3-4, cy-8, cx-r//3+4, cy+4], fill=(120, 0, 0))
        draw.ellipse([cx+r//3-4, cy-8, cx+r//3+4, cy+4], fill=(120, 0, 0))
        # Angry mouth (wavy grimace)
        mouth_y = cy + r // 3
        pts = []
        for mx in range(-r//3, r//3 + 1, 3):
            my = mouth_y + int(math.sin(mx * 0.3 + t * 5) * 3) - 5
            pts.append((cx + mx, my))
        if len(pts) > 1:
            draw.line(pts, fill=(120, 0, 0), width=2)
        # Steam/heat waves rising
        for i in range(3):
            wx = cx - 20 + i * 20
            for dy in range(0, 25, 3):
                wy = cy - r - 15 - dy + int(math.sin(t * 4 + i + dy * 0.3) * 3)
                alpha = max(0, 200 - dy * 8)
                draw.point((wx + int(math.sin(t*2 + dy*0.2 + i)*4), wy), fill=(255, alpha, 0))

    elif 'sun' in cond or 'clear' in cond:
        # ── Sunny: bright yellow sun with rotating rays + grass blowing ──
        r = min(iw, ih) // 2 - 20
        sun_cy = cy - 15
        # Rotating rays
        for i in range(12):
            angle = t * 0.5 + i * math.pi / 6
            x1 = cx + int(math.cos(angle) * (r + 5))
            y1 = sun_cy + int(math.sin(angle) * (r + 5))
            x2 = cx + int(math.cos(angle) * (r + 18 + math.sin(t * 2 + i) * 5))
            y2 = sun_cy + int(math.sin(angle) * (r + 18 + math.sin(t * 2 + i) * 5))
            draw.line([(x1, y1), (x2, y2)], fill=(255, 220, 50), width=2)
        # Sun body
        draw.ellipse([cx-r, sun_cy-r, cx+r, sun_cy+r], fill=(255, 220, 50))
        # Happy face
        draw.ellipse([cx-r//4-3, sun_cy-4, cx-r//4+3, sun_cy+4], fill=(200, 150, 0))
        draw.ellipse([cx+r//4-3, sun_cy-4, cx+r//4+3, sun_cy+4], fill=(200, 150, 0))
        draw.arc([cx-r//3, sun_cy+2, cx+r//3, sun_cy+r//2], 0, 180, fill=(200, 150, 0), width=2)
        # Grass at bottom
        grass_y = iy + ih - 5
        if not _grass_blades:
            _init_grass(ix + 5, grass_y, iw - 10, 20)
        for gx, gh, phase in _grass_blades:
            sway = math.sin(t * 2 + phase) * 6
            draw.line([(gx, grass_y), (gx + sway, grass_y - gh)], fill=(30, 180, 50), width=2)

    elif 'cloud' in cond or 'overcast' in cond:
        # ── Cloudy: multiple drifting clouds ──
        for i, (off_x, off_y, size) in enumerate([(0, 0, 1.0), (-30, 20, 0.7), (25, 25, 0.8)]):
            drift = math.sin(t * 0.3 + i * 2) * 8
            shade = 100 + i * 20
            _draw_cloud(draw, cx - 35 + off_x + drift, iy + 15 + off_y,
                        int(70 * size), int(30 * size), (shade, shade+10, shade+20))

    else:
        # ── Partly cloudy default: sun peeking behind cloud ──
        sun_x, sun_y = cx + 15, iy + 25
        r = 18
        for i in range(8):
            angle = t * 0.4 + i * math.pi / 4
            x1 = sun_x + int(math.cos(angle) * (r + 3))
            y1 = sun_y + int(math.sin(angle) * (r + 3))
            x2 = sun_x + int(math.cos(angle) * (r + 12))
            y2 = sun_y + int(math.sin(angle) * (r + 12))
            draw.line([(x1, y1), (x2, y2)], fill=(200, 180, 40), width=2)
        draw.ellipse([sun_x-r, sun_y-r, sun_x+r, sun_y+r], fill=(255, 220, 60))
        _draw_cloud(draw, cx - 40, iy + 30, 75, 35, (160, 170, 190))

def _draw_cloud(draw, x, y, w, h, color):
    """Draw a puffy cloud shape."""
    # Three overlapping ellipses
    draw.ellipse([x, y + h//3, x + w//2, y + h], fill=color)
    draw.ellipse([x + w//4, y, x + w*3//4, y + h*2//3], fill=color)
    draw.ellipse([x + w//2, y + h//4, x + w, y + h], fill=color)
    # Bottom rectangle to fill gaps
    draw.rectangle([x + w//6, y + h//2, x + w - w//6, y + h], fill=color)

def _animate_rain(draw, ix, iy, iw, ih, color):
    """Animate falling rain drops."""
    global _rain_drops
    for i in range(len(_rain_drops)):
        rx, ry, spd, length = _rain_drops[i]
        ry += spd
        if ry > iy + ih:
            ry = iy
            rx = random.randint(ix, ix + iw)
        _rain_drops[i] = (rx, ry, spd, length)
        draw.line([(rx, ry), (rx - 2, ry + length)], fill=color, width=1)

def init():
    """Initialize clock state. Fetches weather on first call."""
    global _weather, _weather_last_fetch, _weather_error
    _weather = None
    _weather_last_fetch = 0.0
    _weather_error = None
    _fetch_weather()

def _fetch_weather():
    """Fetch weather from wttr.in. Updates module-level state."""
    global _weather, _weather_last_fetch, _weather_error
    try:
        import requests
        resp = requests.get('http://wttr.in/?format=j1', timeout=10, headers={
            'User-Agent': 'tinyscreen-clock/1.0',
            'Accept': 'application/json',
        })
        resp.raise_for_status()
        data = resp.json()

        current = data.get('current_condition', [{}])[0]
        _weather = {
            'temp_c':     current.get('temp_C', '?'),
            'temp_f':     current.get('temp_F', '?'),
            'feels_c':    current.get('FeelsLikeC', '?'),
            'feels_f':    current.get('FeelsLikeF', '?'),
            'condition':  current.get('weatherDesc', [{'value': 'Unknown'}])[0].get('value', 'Unknown'),
            'humidity':   current.get('humidity', '?'),
            'wind_kph':   current.get('windspeedKmph', '?'),
            'wind_mph':   current.get('windspeedMiles', '?'),
            'wind_dir':   current.get('winddir16Point', ''),
            'pressure':   current.get('pressure', '?'),
            'visibility': current.get('visibility', '?'),
            'uv_index':   current.get('uvIndex', '?'),
            'cloud_cover': current.get('cloudcover', '?'),
        }

        # Try to get location from nearest area
        nearest = data.get('nearest_area', [{}])[0]
        area = nearest.get('areaName', [{'value': ''}])[0].get('value', '')
        country = nearest.get('country', [{'value': ''}])[0].get('value', '')
        _weather['location'] = f"{area}, {country}" if area else "Unknown"

        _weather_error = None
        _weather_last_fetch = time.monotonic()
    except ImportError:
        _weather_error = "requests not installed"
        _weather_last_fetch = time.monotonic()
    except Exception as e:
        _weather_error = str(e)[:60]
        _weather_last_fetch = time.monotonic()

# ── System info helpers ─────────────────────────────────────────────
def _read_hostname():
    try:
        with open('/etc/hostname') as f:
            return f.read().strip()
    except Exception:
        return 'unknown'

def _read_uptime():
    try:
        with open('/proc/uptime') as f:
            secs = float(f.read().split()[0])
        days = int(secs // 86400)
        hours = int((secs % 86400) // 3600)
        mins = int((secs % 3600) // 60)
        if days > 0:
            return f"{days}d {hours}h {mins}m"
        return f"{hours}h {mins}m"
    except Exception:
        return '?'

def _read_cpu_pct():
    """Quick single-read CPU usage estimate from /proc/stat."""
    try:
        with open('/proc/stat') as f:
            line = f.readline()
        parts = line.split()
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        # Use a cached previous reading
        if not hasattr(_read_cpu_pct, '_prev'):
            _read_cpu_pct._prev = (idle, total)
            return 0.0
        d_idle = idle - _read_cpu_pct._prev[0]
        d_total = total - _read_cpu_pct._prev[1]
        _read_cpu_pct._prev = (idle, total)
        if d_total > 0:
            return 100.0 * (1.0 - d_idle / d_total)
        return 0.0
    except Exception:
        return 0.0

def _read_mem_pct():
    try:
        info = {}
        with open('/proc/meminfo') as f:
            for line in f:
                parts = line.split()
                info[parts[0].rstrip(':')] = int(parts[1])
        total = info['MemTotal']
        avail = info.get('MemAvailable', info.get('MemFree', 0))
        used = total - avail
        return 100.0 * used / total if total else 0
    except Exception:
        return 0.0

def _read_load():
    try:
        with open('/proc/loadavg') as f:
            parts = f.read().split()
        return f"{parts[0]} {parts[1]} {parts[2]}"
    except Exception:
        return '?'

def _read_ip():
    try:
        out = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=2)
        ips = out.stdout.strip().split()
        return ips[0] if ips else '--'
    except Exception:
        return '--'

# ── Drawing helpers ─────────────────────────────────────────────────
def _draw_panel(draw, x, y, w, h, title=""):
    """Draw a panel background with optional title."""
    draw.rectangle([x, y, x + w, y + h], fill=PANEL_BG, outline=BORDER)
    draw.rectangle([x, y, x + w, y + 1], fill=(0, 80, 140))
    if title:
        draw.text((x + 8, y + 4), title, fill=ACCENT, font=font(13))

def _pct_color(pct):
    if pct < 50:
        return GREEN
    if pct < 75:
        return YELLOW
    if pct < 90:
        return ORANGE
    return RED


# ── Main render ─────────────────────────────────────────────────────
def render_frame(w=1920, h=440):
    """Render one frame of the clock dashboard. Returns PIL Image."""
    now = time.monotonic()

    # Fetch weather periodically
    if now - _weather_last_fetch >= WEATHER_INTERVAL:
        _fetch_weather()

    img = Image.new('RGB', (w, h), BG)
    draw = ImageDraw.Draw(img)

    pad = 6
    py0 = pad
    ph = h - pad * 2

    # ═══════════════════════════════════════════════════════════════
    # Panel 1: Clock (left 40%)
    # ═══════════════════════════════════════════════════════════════
    p1w = int(w * 0.40)
    _draw_panel(draw, pad, py0, p1w, ph, "CLOCK")

    # Time — massive centered display (12-hour format)
    now_t = time.localtime()
    time_str = time.strftime("%-I:%M:%S %p", now_t)

    # Measure text to center it
    time_font = font(120)
    bbox = draw.textbbox((0, 0), time_str, font=time_font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = pad + (p1w - tw) // 2
    ty = py0 + 40

    # Draw time with subtle glow effect (draw slightly larger dim version behind)
    glow_color = (0, 60, 100)
    draw.text((tx - 1, ty - 1), time_str, fill=glow_color, font=time_font)
    draw.text((tx + 1, ty + 1), time_str, fill=glow_color, font=time_font)
    draw.text((tx, ty), time_str, fill=ACCENT, font=time_font)

    # Colon blink: every other second, dim the colons
    if now_t.tm_sec % 2 == 0:
        # Redraw colons brighter
        pass  # colons are already drawn, blink effect via ACCENT color

    # Date — below time
    date_str = time.strftime("%A, %B %d, %Y", now_t)
    date_font = font(30)
    bbox_d = draw.textbbox((0, 0), date_str, font=date_font)
    dw = bbox_d[2] - bbox_d[0]
    dx = pad + (p1w - dw) // 2
    dy = ty + th + 40
    draw.text((dx, dy), date_str, fill=TEXT, font=date_font)

    # Week number and day of year
    week_str = time.strftime("Week %W  |  Day %j of 365", now_t)
    wf = font(18)
    bbox_w = draw.textbbox((0, 0), week_str, font=wf)
    ww = bbox_w[2] - bbox_w[0]
    wx = pad + (p1w - ww) // 2
    wy = dy + 42
    draw.text((wx, wy), week_str, fill=TEXT_DIM, font=wf)

    # Timezone — clearly show abbreviation (e.g. CDT, CST, EST, PST)
    tz_abbr = time.strftime("%Z", now_t)  # e.g. "CDT" or "CST"
    tz_offset = time.strftime("UTC%z", now_t)  # e.g. "UTC-0500"
    tz_str = f"{tz_abbr}  ({tz_offset})"
    tzf = font(16)
    bbox_tz = draw.textbbox((0, 0), tz_str, font=tzf)
    tzw = bbox_tz[2] - bbox_tz[0]
    tzx = pad + (p1w - tzw) // 2
    tzy = wy + 30
    draw.text((tzx, tzy), tz_str, fill=TEXT_DIM, font=tzf)

    # ═══════════════════════════════════════════════════════════════
    # Panel 2: Weather (middle 30%)
    # ═══════════════════════════════════════════════════════════════
    p2x = pad + p1w + pad
    p2w = int(w * 0.30)
    _draw_panel(draw, p2x, py0, p2w, ph, "WEATHER")

    if _weather:
        wy_base = py0 + 24

        # Location
        draw.text((p2x + 8, wy_base), _weather.get('location', ''),
                  fill=TEXT_DIM, font=font(16))

        # Temperature — large (use Fahrenheit if available, else Celsius)
        temp_f = _weather.get('temp_f')
        feels_f = _weather.get('feels_f')
        if temp_f and temp_f != '?':
            temp_str = f"{temp_f}°F"
            feels_str = f"Feels like {feels_f}°F"
        else:
            temp_str = f"{_weather['temp_c']}°C"
            feels_str = f"Feels like {_weather['feels_c']}°C"
        draw.text((p2x + 8, wy_base + 24), temp_str,
                  fill=ACCENT, font=font(72))

        # Feels like
        draw.text((p2x + 8, wy_base + 105), feels_str,
                  fill=TEXT_DIM, font=font(18))

        # Condition text
        condition = _weather.get('condition', 'Unknown')
        draw.text((p2x + 8, wy_base + 132), condition,
                  fill=TEXT, font=font(24))

        # Animated weather icon (right side of panel)
        icon_x = p2x + p2w - 170
        icon_y = wy_base + 20
        icon_w = 160
        icon_h = 130
        temp_f_val = _weather.get('temp_f', None)
        _draw_animated_icon(draw, icon_x, icon_y, icon_w, icon_h, condition, temp_f_val)

        # Details grid — bottom half
        detail_y = wy_base + 170
        row_h = 34
        col_w = (p2w - 16) // 2

        details = [
            ("Humidity",    f"{_weather['humidity']}%",     CYAN),
            ("Wind",        f"{_weather.get('wind_mph', _weather['wind_kph'])} mph {_weather['wind_dir']}", TEXT),
            ("Pressure",    f"{_weather['pressure']} hPa",  TEXT),
            ("Visibility",  f"{_weather['visibility']} km", TEXT),
            ("UV Index",    str(_weather['uv_index']),      YELLOW),
            ("Cloud Cover", f"{_weather['cloud_cover']}%",  TEXT_DIM),
        ]

        for i, (label, value, color) in enumerate(details):
            col = i % 2
            row = i // 2
            dx = p2x + 8 + col * col_w
            dy = detail_y + row * row_h
            draw.text((dx, dy), label, fill=TEXT_DIM, font=font(13))
            draw.text((dx, dy + 16), value, fill=color, font=font(17))

    elif _weather_error:
        draw.text((p2x + 8, py0 + 40), "Weather unavailable",
                  fill=RED, font=font(20))
        draw.text((p2x + 8, py0 + 70), _weather_error,
                  fill=TEXT_DIM, font=font(14))
        draw.text((p2x + 8, py0 + 95), f"Retry in {int(WEATHER_INTERVAL - (now - _weather_last_fetch))}s",
                  fill=TEXT_DIM, font=font(14))
    else:
        draw.text((p2x + 8, py0 + 40), "Fetching weather...",
                  fill=TEXT_DIM, font=font(20))

    # ═══════════════════════════════════════════════════════════════
    # Panel 3: System Info (right 30%)
    # ═══════════════════════════════════════════════════════════════
    p3x = p2x + p2w + pad
    p3w = w - p3x - pad
    _draw_panel(draw, p3x, py0, p3w, ph, "SYSTEM")

    sy = py0 + 24
    row_gap = 48

    # Hostname — large
    hostname = _read_hostname()
    draw.text((p3x + 8, sy), hostname, fill=ACCENT, font=font(36))

    # Uptime
    sy_info = sy + 52
    draw.text((p3x + 8, sy_info), "UPTIME", fill=TEXT_DIM, font=font(13))
    draw.text((p3x + 90, sy_info), _read_uptime(), fill=TEXT, font=font(18))

    # CPU
    sy_cpu = sy_info + row_gap
    cpu_pct = _read_cpu_pct()
    draw.text((p3x + 8, sy_cpu), "CPU", fill=TEXT_DIM, font=font(13))
    draw.text((p3x + 90, sy_cpu - 4), f"{cpu_pct:.0f}%",
              fill=_pct_color(cpu_pct), font=font(28))
    # CPU bar
    bar_x = p3x + 8
    bar_w = p3w - 16
    bar_y = sy_cpu + 30
    bar_h = 14
    draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h],
                   fill=PANEL_BG, outline=BORDER)
    fill_w = max(0, int(bar_w * min(cpu_pct, 100) / 100))
    if fill_w > 0:
        color = _pct_color(cpu_pct)
        dim = tuple(max(0, c - 60) for c in color)
        draw.rectangle([bar_x + 1, bar_y + 1, bar_x + fill_w, bar_y + bar_h - 1],
                       fill=dim)
        tip_w = min(3, fill_w)
        draw.rectangle([bar_x + fill_w - tip_w, bar_y + 1,
                        bar_x + fill_w, bar_y + bar_h - 1], fill=color)

    # RAM
    sy_ram = bar_y + 24
    mem_pct = _read_mem_pct()
    draw.text((p3x + 8, sy_ram), "RAM", fill=TEXT_DIM, font=font(13))
    draw.text((p3x + 90, sy_ram - 4), f"{mem_pct:.0f}%",
              fill=_pct_color(mem_pct), font=font(28))
    # RAM bar
    bar_y2 = sy_ram + 30
    draw.rectangle([bar_x, bar_y2, bar_x + bar_w, bar_y2 + bar_h],
                   fill=PANEL_BG, outline=BORDER)
    fill_w2 = max(0, int(bar_w * min(mem_pct, 100) / 100))
    if fill_w2 > 0:
        color2 = _pct_color(mem_pct)
        dim2 = tuple(max(0, c - 60) for c in color2)
        draw.rectangle([bar_x + 1, bar_y2 + 1, bar_x + fill_w2, bar_y2 + bar_h - 1],
                       fill=dim2)
        tip_w2 = min(3, fill_w2)
        draw.rectangle([bar_x + fill_w2 - tip_w2, bar_y2 + 1,
                        bar_x + fill_w2, bar_y2 + bar_h - 1], fill=color2)

    # IP Address
    sy_ip = bar_y2 + 28
    draw.text((p3x + 8, sy_ip), "IP", fill=TEXT_DIM, font=font(13))
    draw.text((p3x + 90, sy_ip), _read_ip(), fill=GREEN, font=font(18))

    # Load
    sy_load = sy_ip + 32
    draw.text((p3x + 8, sy_load), "LOAD", fill=TEXT_DIM, font=font(13))
    draw.text((p3x + 90, sy_load), _read_load(), fill=TEXT, font=font(18))

    # Kernel
    sy_kern = sy_load + 32
    try:
        with open('/proc/version') as f:
            kern = f.read().split()[2]
    except Exception:
        kern = '?'
    draw.text((p3x + 8, sy_kern), "KERNEL", fill=TEXT_DIM, font=font(13))
    draw.text((p3x + 90, sy_kern), kern[:28], fill=TEXT_DIM, font=font(16))

    # Bottom accent line
    draw.rectangle([0, h - 2, w, h], fill=(0, 80, 140))

    return img


# ── Standalone mode ─────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    if '--once' in sys.argv:
        init()
        time.sleep(0.1)
        _read_cpu_pct()      # warm up CPU delta
        time.sleep(0.5)
        img = render_frame()
        img.save('/tmp/clock.png')
        print("Saved to /tmp/clock.png")
    else:
        print("Usage:")
        print("  python3 clock.py --once   # save one frame to /tmp/clock.png")
        print("  Use via tinyscreen daemon for live display")
