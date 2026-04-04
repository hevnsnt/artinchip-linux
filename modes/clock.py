#!/usr/bin/env python3
"""
tinyscreen clock — full-screen weather animations with floating clock overlay.

Weather animations fill the entire 1920x440 display edge-to-edge.
Clock, weather data, and system info float over the animation with
heavy drop shadows for readability.

Designed for 1920x440 stretched bar LCDs, rendered at ~15fps with PIL/Pillow.
"""

import os
import subprocess
import sys
import time

from PIL import Image, ImageDraw

# Ensure scenes package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scenes import SCENE_MAP
from scenes.engine import font, draw_text_shadow, color_grade

# ---------------------------------------------------------------------------
# UI colors (for text overlays)
# ---------------------------------------------------------------------------
ACCENT = (0, 200, 255)
TEXT = (200, 210, 225)
TEXT_DIM = (160, 170, 190)
TEXT_BRIGHT = (240, 245, 255)
GREEN = (0, 220, 100)
RED = (255, 60, 60)
YELLOW = (255, 200, 0)
ORANGE = (255, 140, 0)

# ---------------------------------------------------------------------------
# Weather state
# ---------------------------------------------------------------------------
_weather: dict | None = None
_weather_last_fetch: float = 0.0
_weather_error: str | None = None
WEATHER_INTERVAL = 300

# ---------------------------------------------------------------------------
# Scene state
# ---------------------------------------------------------------------------
_current_scene = None
_current_scene_type: str | None = None

# ---------------------------------------------------------------------------
# System info caches
# ---------------------------------------------------------------------------
_cpu_smooth: float = 0.0
_cached_ip: str = '--'
_cached_ip_time: float = 0.0
_cached_hostname: str | None = None
_cached_kernel: str | None = None

# ---------------------------------------------------------------------------
# Weather fetch
# ---------------------------------------------------------------------------

def _fetch_weather():
    global _weather, _weather_last_fetch, _weather_error
    global _current_scene, _current_scene_type
    try:
        import requests
        resp = requests.get('http://wttr.in/?format=j1', timeout=10, headers={
            'User-Agent': 'tinyscreen-clock/3.0',
            'Accept': 'application/json',
        })
        resp.raise_for_status()
        data = resp.json()

        current = data.get('current_condition', [{}])[0]
        new_weather = {
            'temp_c':      current.get('temp_C', '?'),
            'temp_f':      current.get('temp_F', '?'),
            'feels_c':     current.get('FeelsLikeC', '?'),
            'feels_f':     current.get('FeelsLikeF', '?'),
            'condition':   current.get('weatherDesc', [{'value': 'Unknown'}])[0].get('value', 'Unknown'),
            'humidity':    current.get('humidity', '?'),
            'wind_kph':    current.get('windspeedKmph', '?'),
            'wind_mph':    current.get('windspeedMiles', '?'),
            'wind_dir':    current.get('winddir16Point', ''),
            'pressure':    current.get('pressure', '?'),
            'visibility':  current.get('visibility', '?'),
            'uv_index':    current.get('uvIndex', '?'),
            'cloud_cover': current.get('cloudcover', '?'),
        }

        nearest = data.get('nearest_area', [{}])[0]
        area = nearest.get('areaName', [{'value': ''}])[0].get('value', '')
        region = nearest.get('region', [{'value': ''}])[0].get('value', '')
        new_weather['location'] = f"{area}, {region}" if area else "Unknown"

        _weather = new_weather
        _weather_error = None
        _weather_last_fetch = time.monotonic()
    except ImportError:
        _weather_error = "requests library not installed"
        _weather_last_fetch = time.monotonic()
    except Exception as e:
        _weather_error = str(e)[:60]
        _weather_last_fetch = time.monotonic()


# ---------------------------------------------------------------------------
# Condition classification
# ---------------------------------------------------------------------------

def _classify_condition(condition: str, temp_f) -> str:
    cond = condition.lower()
    is_hot = False
    try:
        if temp_f and float(temp_f) >= 95:
            is_hot = True
    except (ValueError, TypeError):
        pass

    if is_hot:
        return 'hot'
    if 'thunder' in cond or 'storm' in cond:
        return 'thunder'
    if 'snow' in cond or 'blizzard' in cond or 'sleet' in cond or 'ice' in cond:
        return 'snow'
    if 'rain' in cond or 'drizzle' in cond or 'shower' in cond:
        return 'rain'
    if 'fog' in cond or 'mist' in cond or 'haze' in cond:
        return 'fog'
    if 'overcast' in cond:
        return 'overcast'
    if 'cloudy' in cond and 'partly' not in cond:
        return 'overcast'
    if 'partly' in cond or 'cloud' in cond:
        return 'partly_cloudy'
    if 'sun' in cond or 'clear' in cond:
        return 'sunny'
    return 'partly_cloudy'


def _get_or_create_scene(scene_type: str, w: int, h: int):
    """Get current scene or create a new one if type changed."""
    global _current_scene, _current_scene_type
    if _current_scene_type != scene_type or _current_scene is None:
        if _current_scene is not None:
            _current_scene.cleanup()
        scene_cls = SCENE_MAP.get(scene_type)
        if scene_cls is None:
            scene_cls = SCENE_MAP.get('partly_cloudy')
        _current_scene = scene_cls(w, h)
        _current_scene_type = scene_type
    return _current_scene


# ---------------------------------------------------------------------------
# System info helpers
# ---------------------------------------------------------------------------

def _read_hostname() -> str:
    global _cached_hostname
    if _cached_hostname is not None:
        return _cached_hostname
    try:
        with open('/etc/hostname') as f:
            _cached_hostname = f.read().strip()
    except Exception:
        _cached_hostname = 'unknown'
    return _cached_hostname


def _read_uptime() -> str:
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


def _read_cpu_pct() -> float:
    global _cpu_smooth
    try:
        with open('/proc/stat') as f:
            line = f.readline()
        parts = line.split()
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        if not hasattr(_read_cpu_pct, '_prev'):
            _read_cpu_pct._prev = (idle, total)
            return 0.0
        d_idle = idle - _read_cpu_pct._prev[0]
        d_total = total - _read_cpu_pct._prev[1]
        _read_cpu_pct._prev = (idle, total)
        raw = 100.0 * (1.0 - d_idle / d_total) if d_total > 0 else 0.0
        _cpu_smooth = _cpu_smooth * 0.92 + raw * 0.08
        return _cpu_smooth
    except Exception:
        return 0.0


def _read_mem_pct() -> float:
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


def _read_load() -> str:
    try:
        with open('/proc/loadavg') as f:
            parts = f.read().split()
        return f"{parts[0]} {parts[1]} {parts[2]}"
    except Exception:
        return '?'


def _read_ip() -> str:
    global _cached_ip, _cached_ip_time
    now = time.monotonic()
    if now - _cached_ip_time < 30:
        return _cached_ip
    try:
        out = subprocess.run(['hostname', '-I'], capture_output=True,
                             text=True, timeout=2)
        ips = out.stdout.strip().split()
        _cached_ip = ips[0] if ips else '--'
    except Exception:
        _cached_ip = '--'
    _cached_ip_time = now
    return _cached_ip


def _read_kernel() -> str:
    global _cached_kernel
    if _cached_kernel is not None:
        return _cached_kernel
    try:
        with open('/proc/version') as f:
            _cached_kernel = f.read().split()[2]
    except Exception:
        _cached_kernel = '?'
    return _cached_kernel


def _pct_color(pct: float) -> tuple:
    if pct < 50:
        return GREEN
    if pct < 75:
        return YELLOW
    if pct < 90:
        return ORANGE
    return RED


# ---------------------------------------------------------------------------
# Floating text overlay rendering
# ---------------------------------------------------------------------------

def _render_clock_overlay(draw: ImageDraw.Draw, w: int, h: int):
    """Draw the clock in the left portion, vertically centered."""
    now_t = time.localtime()
    margin = 28

    # Time: massive 12-hour
    hour_min = time.strftime("%-I:%M", now_t)
    ampm = time.strftime("%p", now_t)
    secs = time.strftime("%S", now_t)

    time_font = font(140, bold=True)
    ampm_font = font(42, bold=True)
    sec_font = font(32)

    hm_bbox = draw.textbbox((0, 0), hour_min, font=time_font)
    hm_w = hm_bbox[2] - hm_bbox[0]
    hm_h = hm_bbox[3] - hm_bbox[1]

    # Vertically center the clock block (time + date + tz = ~250px total)
    block_h = hm_h + 50 + 34 + 24  # time + gap + date + tz
    time_y = (h - block_h) // 2

    draw_text_shadow(draw, margin, time_y, hour_min, ACCENT, time_font, shadow_offset=4)

    # AM/PM to the right of time
    right_x = margin + hm_w + 12
    draw_text_shadow(draw, right_x, time_y + 15, ampm, (0, 170, 230), ampm_font, shadow_offset=2)
    draw_text_shadow(draw, right_x + 4, time_y + 65, secs, TEXT_DIM, sec_font, shadow_offset=2)

    # Date below time, centered under the time text
    date_str = time.strftime("%A, %B %d, %Y", now_t)
    date_font = font(28)
    date_y = time_y + hm_h + 36
    d_bbox = draw.textbbox((0, 0), date_str, font=date_font)
    d_w = d_bbox[2] - d_bbox[0]
    time_cx = margin + hm_w // 2
    draw_text_shadow(draw, time_cx - d_w // 2, date_y, date_str, TEXT, date_font, shadow_offset=2)

    # Timezone, centered under the date
    tz_abbr = time.strftime("%Z", now_t)
    tz_offset = time.strftime("UTC%z", now_t)
    tz_str = f"{tz_abbr} ({tz_offset})"
    tz_font = font(18)
    tz_bbox = draw.textbbox((0, 0), tz_str, font=tz_font)
    tz_w = tz_bbox[2] - tz_bbox[0]
    draw_text_shadow(draw, time_cx - tz_w // 2, date_y + 38, tz_str, TEXT_DIM, tz_font, shadow_offset=2)


def _render_weather_overlay(draw: ImageDraw.Draw, w: int, h: int):
    """Draw weather data in the center, vertically centered."""
    if not _weather:
        cy = h // 2
        if _weather_error:
            draw_text_shadow(draw, w // 2 - 100, cy - 20, "Weather unavailable",
                             RED, font(20), shadow_offset=2)
            draw_text_shadow(draw, w // 2 - 100, cy + 10, _weather_error,
                             TEXT_DIM, font(14), shadow_offset=2)
        else:
            draw_text_shadow(draw, w // 2 - 80, cy, "Fetching weather...",
                             TEXT_DIM, font(20), shadow_offset=2)
        return

    cx = w // 2

    # Total block height: location(20) + gap(8) + temp(70) + gap(6) + feels(20) +
    #   gap(10) + condition(26) + gap(12) + details(18) + gap(12) + details2(18) = ~220
    block_h = 240
    y0 = (h - block_h) // 2

    # Location
    loc = _weather.get('location', '')
    if loc:
        loc_font = font(18)
        loc_bbox = draw.textbbox((0, 0), loc, font=loc_font)
        loc_w = loc_bbox[2] - loc_bbox[0]
        draw_text_shadow(draw, cx - loc_w // 2, y0, loc,
                         (200, 210, 230), loc_font, shadow_offset=2)

    # Temperature - large
    temp_f = _weather.get('temp_f')
    feels_f = _weather.get('feels_f')
    if temp_f and temp_f != '?':
        temp_str = f"{temp_f}\u00b0F"
        feels_str = f"Feels like {feels_f}\u00b0F"
    else:
        temp_str = f"{_weather.get('temp_c', '?')}\u00b0C"
        feels_str = f"Feels like {_weather.get('feels_c', '?')}\u00b0C"

    temp_font = font(80, bold=True)
    temp_bbox = draw.textbbox((0, 0), temp_str, font=temp_font)
    temp_w = temp_bbox[2] - temp_bbox[0]
    temp_y = y0 + 28
    draw_text_shadow(draw, cx - temp_w // 2, temp_y, temp_str,
                     (255, 255, 255), temp_font, shadow_offset=3)

    # Condition
    condition = _weather.get('condition', 'Unknown')
    cond_font = font(24, bold=True)
    cond_bbox = draw.textbbox((0, 0), condition, font=cond_font)
    cond_w = cond_bbox[2] - cond_bbox[0]
    cond_y = temp_y + 88
    draw_text_shadow(draw, cx - cond_w // 2, cond_y, condition,
                     TEXT_BRIGHT, cond_font, shadow_offset=2)

    # Feels like
    feels_font = font(24)
    feels_bbox = draw.textbbox((0, 0), feels_str, font=feels_font)
    feels_w = feels_bbox[2] - feels_bbox[0]
    feels_y = cond_y + 34
    draw_text_shadow(draw, cx - feels_w // 2, feels_y, feels_str,
                     (200, 210, 225), feels_font, shadow_offset=2)

    # Detail rows
    humidity = _weather.get('humidity', '?')
    wind_mph = _weather.get('wind_mph', _weather.get('wind_kph', '?'))
    wind_dir = _weather.get('wind_dir', '')
    pressure = _weather.get('pressure', '?')

    detail1 = f"Humidity {humidity}%  \u00b7  Wind {wind_mph} mph {wind_dir}"
    detail2 = f"Pressure {pressure} hPa"

    detail_font = font(22)
    d1_bbox = draw.textbbox((0, 0), detail1, font=detail_font)
    d1_w = d1_bbox[2] - d1_bbox[0]
    d1_y = feels_y + 32
    draw_text_shadow(draw, cx - d1_w // 2, d1_y, detail1,
                     (190, 200, 220), detail_font, shadow_offset=2)

    d2_bbox = draw.textbbox((0, 0), detail2, font=detail_font)
    d2_w = d2_bbox[2] - d2_bbox[0]
    draw_text_shadow(draw, cx - d2_w // 2, d1_y + 28, detail2,
                     (190, 200, 220), detail_font, shadow_offset=2)


def _render_sysinfo_overlay(draw: ImageDraw.Draw, w: int, h: int):
    """Draw system info in the right portion, vertically centered."""
    right_margin = w - 28
    info_font = font(23)

    # Collect all lines to measure total height
    hostname = _read_hostname()
    uptime = _read_uptime()
    cpu_pct = _read_cpu_pct()
    mem_pct = _read_mem_pct()
    ip = _read_ip()
    load = _read_load()
    kern = _read_kernel()[:30]

    lines = [
        (hostname, ACCENT, font(34, bold=True), 44),
        (f"Up {uptime}", TEXT, info_font, 34),
        (f"CPU {cpu_pct:.0f}%", _pct_color(cpu_pct), font(24, bold=True), 34),
        (f"RAM {mem_pct:.0f}%", _pct_color(mem_pct), font(24, bold=True), 34),
        (ip, (200, 215, 230), info_font, 32),
        (f"Load {load}", (200, 210, 225), info_font, 32),
        (kern, (180, 190, 210), font(19), 28),
    ]

    total_h = sum(gap for _, _, _, gap in lines)
    y = (h - total_h) // 2

    for text, color, f, gap in lines:
        l_bbox = draw.textbbox((0, 0), text, font=f)
        l_w = l_bbox[2] - l_bbox[0]
        shadow = 2 if f.size >= 30 else 1
        draw_text_shadow(draw, right_margin - l_w, y, text, color, f,
                         shadow_offset=shadow)
        y += gap


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_anim_start: float = 0.0


def init():
    """Initialize clock state."""
    global _weather, _weather_last_fetch, _weather_error
    global _current_scene, _current_scene_type, _anim_start
    _weather = None
    _weather_last_fetch = 0.0
    _weather_error = None
    _current_scene = None
    _current_scene_type = None
    _anim_start = time.monotonic()
    _fetch_weather()


def render_frame(w: int = 1920, h: int = 440) -> Image.Image:
    """Render one frame of the clock display. Returns PIL Image (RGB)."""
    now = time.monotonic()

    # Periodic weather refresh
    if now - _weather_last_fetch >= WEATHER_INTERVAL:
        _fetch_weather()

    # Animation time
    if _anim_start == 0.0:
        init()
    t = now - _anim_start

    # Determine scene type
    condition = ''
    temp_f = None
    if _weather:
        condition = _weather.get('condition', 'Unknown')
        temp_f = _weather.get('temp_f')

    scene_type = _classify_condition(condition, temp_f) if condition else 'partly_cloudy'

    # Get or create scene
    scene = _get_or_create_scene(scene_type, w, h)

    # Render weather scene (RGBA)
    try:
        scene_img = scene.render(t, _weather or {})
    except Exception:
        scene_img = Image.new('RGBA', (w, h), (10, 12, 18, 255))

    # Convert to RGB for final output
    img = Image.new('RGB', (w, h), (10, 12, 18))
    img.paste(scene_img, (0, 0), scene_img)

    draw = ImageDraw.Draw(img)

    # Floating text overlays
    _render_clock_overlay(draw, w, h)
    _render_weather_overlay(draw, w, h)
    _render_sysinfo_overlay(draw, w, h)

    return img


# ---------------------------------------------------------------------------
# Standalone mode
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    if '--once' in sys.argv:
        init()
        time.sleep(0.1)
        _read_cpu_pct()
        time.sleep(0.5)
        img = render_frame()
        img.save('/tmp/clock.png')
        print("Saved to /tmp/clock.png")
    elif '--anim' in sys.argv:
        init()
        time.sleep(0.1)
        _read_cpu_pct()
        time.sleep(0.3)
        print("Rendering 30 frames...")
        for i in range(30):
            img = render_frame()
            img.save(f'/tmp/clock_{i:03d}.png')
        print("Saved /tmp/clock_000.png through /tmp/clock_029.png")
    elif '--scene' in sys.argv:
        # Force a specific scene type for testing
        idx = sys.argv.index('--scene')
        if idx + 1 < len(sys.argv):
            scene_type = sys.argv[idx + 1]
            scene_cls = SCENE_MAP.get(scene_type)
            if scene_cls:
                scene = scene_cls(1920, 440)
                print(f"Rendering {scene_type} scene...")
                for i in range(30):
                    scene_img = scene.render(i * 0.067, {})
                    scene_img.convert('RGB').save(f'/tmp/clock_{scene_type}_{i:03d}.png')
                print(f"Saved 30 frames to /tmp/clock_{scene_type}_*.png")
            else:
                print(f"Unknown scene: {scene_type}")
                print(f"Available: {', '.join(SCENE_MAP.keys())}")
        else:
            print("Usage: clock.py --scene <type>")
    else:
        print("Usage:")
        print("  python3 clock.py --once              # save one frame")
        print("  python3 clock.py --anim              # save 30 frames")
        print("  python3 clock.py --scene rain         # test specific scene")
        print("  Use via tinyscreen daemon for live display")
