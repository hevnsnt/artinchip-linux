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

# Weather condition to ASCII icon mapping
_WEATHER_ICONS = {
    'sunny':          ['    \\   /   ', '     .-.    ', '  - (   ) - ', '     `-`    ', '    /   \\   '],
    'clear':          ['    \\   /   ', '     .-.    ', '  - (   ) - ', '     `-`    ', '    /   \\   '],
    'partly cloudy':  ['   \\  /     ', ' _ /\"\".-.   ', '   \\_(   ). ', '   /(___(__)', '            '],
    'cloudy':         ['            ', '     .--.   ', '  .-(    ). ', ' (___.__)__)', '            '],
    'overcast':       ['            ', '     .--.   ', '  .-(    ). ', ' (___.__)__)', '            '],
    'rain':           ['     .-.    ', '    (   ).  ', '   (___(__))', '   / / / /  ', '  / / / /   '],
    'light rain':     ['     .-.    ', '    (   ).  ', '   (___(__))', '    / / /   ', '            '],
    'heavy rain':     ['     .-.    ', '    (   ).  ', '   (___(__))', '  ////////  ', '  ////////  '],
    'snow':           ['     .-.    ', '    (   ).  ', '   (___(__))', '   * * * *  ', '  * * * *   '],
    'thunder':        ['     .-.    ', '    (   ).  ', '   (___(__))', '   _/  _/   ', '   /  /     '],
    'fog':            ['            ', ' _ - _ - _ -', '  _ - _ - _ ', ' _ - _ - _ -', '            '],
    'mist':           ['            ', ' _ - _ - _ -', '  _ - _ - _ ', ' _ - _ - _ -', '            '],
}

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

def _get_weather_icon(condition):
    """Return ASCII art lines for a weather condition, or None."""
    cond_lower = condition.lower()
    for key, lines in _WEATHER_ICONS.items():
        if key in cond_lower:
            return lines
    # Fallback: check for partial matches
    if 'rain' in cond_lower or 'drizzle' in cond_lower:
        return _WEATHER_ICONS['rain']
    if 'snow' in cond_lower or 'sleet' in cond_lower:
        return _WEATHER_ICONS['snow']
    if 'cloud' in cond_lower:
        return _WEATHER_ICONS['cloudy']
    if 'sun' in cond_lower or 'clear' in cond_lower:
        return _WEATHER_ICONS['sunny']
    if 'thunder' in cond_lower or 'storm' in cond_lower:
        return _WEATHER_ICONS['thunder']
    if 'fog' in cond_lower or 'mist' in cond_lower or 'haze' in cond_lower:
        return _WEATHER_ICONS['fog']
    return None

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

        # Weather ASCII icon (right side of panel)
        icon_lines = _get_weather_icon(condition)
        if icon_lines:
            icon_x = p2x + p2w - 170
            icon_y = wy_base + 30
            for i, line in enumerate(icon_lines):
                draw.text((icon_x, icon_y + i * 18), line,
                          fill=YELLOW, font=font(14))

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
