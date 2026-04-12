#!/usr/bin/env python3
"""
tinyscreen system monitor — renders a live hardware dashboard to the bar display.

Reads all data from /proc and /sys directly (no psutil dependency).
Designed for 1920x440 stretched bar LCDs.

Usage:
  tinyscreen-sysmon              # run via tinyscreen daemon
  python3 sysmon.py --once       # render one frame to /tmp/sysmon.png (for testing)
"""

import os
import re
import time
import subprocess
from PIL import Image, ImageDraw, ImageFont
from modes.glow import (paste_hero_text, paste_glow_bar, paste_panel,
                         glow_accent_line, glow_arc, glow_line, glow_rect)

# ── Colors (vivid, saturated for maximum impact) ──────────────────
BG          = (5, 7, 12)
PANEL_BG    = (10, 14, 24)
ACCENT      = (0, 210, 255)
ACCENT_DIM  = (0, 80, 150)
TEXT        = (220, 225, 240)
TEXT_DIM    = (65, 75, 100)
TEXT_BRIGHT = (252, 254, 255)
GREEN       = (0, 255, 140)
YELLOW      = (255, 225, 0)
RED         = (255, 50, 50)
ORANGE      = (255, 165, 30)
CYAN        = (0, 240, 255)
PURPLE      = (160, 110, 255)

# Pre-computed scanline overlay (created once, reused every frame)
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

# ── Data collection from /proc and /sys ─────────────────────────────
_prev_cpu = None
_prev_net = None
_prev_time = None
_cpu_history = []     # last 60 samples of total CPU %
_net_rx_history = []  # last 60 samples of rx bytes/s
_net_tx_history = []  # last 60 samples of tx bytes/s

def read_cpu():
    """Read per-core CPU usage from /proc/stat. Returns list of per-core percentages."""
    global _prev_cpu
    with open('/proc/stat') as f:
        lines = [l for l in f if l.startswith('cpu')]

    current = {}
    for line in lines:
        parts = line.split()
        name = parts[0]
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
        total = sum(vals)
        current[name] = (idle, total)

    if _prev_cpu is None:
        _prev_cpu = current
        return [0.0] * (len(current) - 1)  # exclude 'cpu' total line

    result = []
    for name in sorted(current.keys()):
        if name == 'cpu':
            continue
        if name in _prev_cpu:
            d_idle = current[name][0] - _prev_cpu[name][0]
            d_total = current[name][1] - _prev_cpu[name][1]
            if d_total > 0:
                result.append(100.0 * (1.0 - d_idle / d_total))
            else:
                result.append(0.0)

    # Total CPU for history
    if 'cpu' in current and 'cpu' in _prev_cpu:
        d_idle = current['cpu'][0] - _prev_cpu['cpu'][0]
        d_total = current['cpu'][1] - _prev_cpu['cpu'][1]
        total_pct = 100.0 * (1.0 - d_idle / d_total) if d_total > 0 else 0
        _cpu_history.append(total_pct)
        if len(_cpu_history) > 60:
            _cpu_history.pop(0)

    _prev_cpu = current
    return result

def read_mem():
    """Returns (used_gb, total_gb, percent)."""
    info = {}
    with open('/proc/meminfo') as f:
        for line in f:
            parts = line.split()
            info[parts[0].rstrip(':')] = int(parts[1])
    total = info['MemTotal']
    avail = info.get('MemAvailable', info.get('MemFree', 0))
    used = total - avail
    return used / 1048576, total / 1048576, 100.0 * used / total if total else 0

def read_temps():
    """Returns dict of label -> temp_c."""
    temps = {}
    # coretemp (CPU)
    for hwmon in sorted(os.listdir('/sys/class/hwmon/')):
        base = f'/sys/class/hwmon/{hwmon}'
        try:
            with open(f'{base}/name') as f:
                name = f.read().strip()
        except FileNotFoundError:
            continue
        if name == 'coretemp':
            i = 1
            while os.path.exists(f'{base}/temp{i}_input'):
                try:
                    with open(f'{base}/temp{i}_input') as f:
                        temp = int(f.read().strip()) / 1000
                    label_path = f'{base}/temp{i}_label'
                    if os.path.exists(label_path):
                        with open(label_path) as f:
                            label = f.read().strip()
                    else:
                        label = f'Temp {i}'
                    temps[label] = temp
                except Exception:
                    pass
                i += 1
    # PCH
    try:
        for hwmon in os.listdir('/sys/class/hwmon/'):
            base = f'/sys/class/hwmon/{hwmon}'
            with open(f'{base}/name') as f:
                name = f.read().strip()
            if 'pch' in name:
                with open(f'{base}/temp1_input') as f:
                    temps['PCH'] = int(f.read().strip()) / 1000
    except Exception:
        pass
    return temps

def read_gpu():
    """Returns dict with gpu temp, util, mem_used, mem_total, name. Or None."""
    try:
        out = subprocess.run(
            ['nvidia-smi', '--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total,name',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=3)
        if out.returncode == 0:
            parts = out.stdout.strip().split(', ')
            return {
                'temp': int(parts[0]),
                'util': int(parts[1]),
                'mem_used': int(parts[2]),
                'mem_total': int(parts[3]),
                'name': parts[4].strip()
            }
    except Exception:
        pass
    return None

def read_net():
    """Returns (rx_bytes_per_sec, tx_bytes_per_sec) for primary interface."""
    global _prev_net, _prev_time
    with open('/proc/net/dev') as f:
        lines = f.readlines()[2:]  # skip headers

    rx = tx = 0
    for line in lines:
        parts = line.split()
        iface = parts[0].rstrip(':')
        if iface in ('lo', 'docker0') or iface.startswith(('br-', 'veth', 'virbr')):
            continue
        rx += int(parts[1])
        tx += int(parts[9])

    now = time.monotonic()
    if _prev_net is None:
        _prev_net = (rx, tx)
        _prev_time = now
        return 0, 0

    dt = now - _prev_time
    if dt <= 0:
        return 0, 0
    rx_s = (rx - _prev_net[0]) / dt
    tx_s = (tx - _prev_net[1]) / dt
    _prev_net = (rx, tx)
    _prev_time = now

    _net_rx_history.append(rx_s)
    _net_tx_history.append(tx_s)
    if len(_net_rx_history) > 60:
        _net_rx_history.pop(0)
    if len(_net_tx_history) > 60:
        _net_tx_history.pop(0)

    return rx_s, tx_s

def read_load():
    with open('/proc/loadavg') as f:
        parts = f.read().split()
    return float(parts[0]), float(parts[1]), float(parts[2])

def read_uptime():
    with open('/proc/uptime') as f:
        secs = float(f.read().split()[0])
    days = int(secs // 86400)
    hours = int((secs % 86400) // 3600)
    mins = int((secs % 3600) // 60)
    if days > 0:
        return f"{days}d {hours}h {mins}m"
    return f"{hours}h {mins}m"

def read_disk():
    """Returns (used_gb, total_gb, percent) for root filesystem."""
    st = os.statvfs('/')
    total = st.f_blocks * st.f_frsize
    free = st.f_bfree * st.f_frsize
    used = total - free
    return used / (1024**3), total / (1024**3), 100.0 * used / total if total else 0

def read_hostname():
    try:
        with open('/etc/hostname') as f:
            return f.read().strip()
    except Exception:
        return 'unknown'

def read_cpu_model():
    try:
        with open('/proc/cpuinfo') as f:
            for line in f:
                if 'model name' in line:
                    return line.split(':')[1].strip()
    except Exception:
        pass
    return 'Unknown CPU'

# ── Cached subprocess helpers ──────────────────────────────────────
_gpu_extra_cache = {'data': None, 'time': 0}

def _get_gpu_extra():
    """Returns (power, clock) from nvidia-smi, cached for 5 seconds."""
    now = time.monotonic()
    if now - _gpu_extra_cache['time'] < 5 and _gpu_extra_cache['data'] is not None:
        return _gpu_extra_cache['data']
    try:
        out = subprocess.run(
            ['nvidia-smi', '--query-gpu=power.draw,clocks.current.graphics',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=2)
        if out.returncode == 0:
            parts = out.stdout.strip().split(', ')
            result = (parts[0].strip(), parts[1].strip())
            _gpu_extra_cache['data'] = result
            _gpu_extra_cache['time'] = now
            return result
    except Exception:
        pass
    return _gpu_extra_cache['data']

_ip_cache = {'data': '--', 'time': 0}

def _get_ip():
    """Returns primary IP from hostname -I, cached for 30 seconds."""
    now = time.monotonic()
    if now - _ip_cache['time'] < 30:
        return _ip_cache['data']
    try:
        out = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=2)
        ips = out.stdout.strip().split()
        result = ips[0] if ips else '--'
    except Exception:
        result = '--'
    _ip_cache['data'] = result
    _ip_cache['time'] = now
    return result

# ── Drawing helpers ─────────────────────────────────────────────────
def pct_color(pct):
    if pct < 50:
        return GREEN
    if pct < 75:
        return YELLOW
    if pct < 90:
        return ORANGE
    return RED

def temp_color(temp):
    if temp < 50:
        return GREEN
    if temp < 70:
        return YELLOW
    if temp < 85:
        return ORANGE
    return RED

def fmt_bytes(b):
    if b < 1024:
        return f"{b:.0f} B/s"
    if b < 1048576:
        return f"{b/1024:.1f} KB/s"
    if b < 1073741824:
        return f"{b/1048576:.1f} MB/s"
    return f"{b/1073741824:.2f} GB/s"

def _lerp_color(c1, c2, t):
    """Linearly interpolate between two RGB tuples."""
    t = max(0.0, min(1.0, t))
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

# ── Gradient background ────────────────────────────────────────────
_gradient_bg_cache = {}

def _draw_gradient_bg(img, w, h):
    """Draw a smooth diagonal gradient background with vignette. Cached."""
    if (w, h) not in _gradient_bg_cache:
        top_left = (10, 12, 20)
        bot_right = (4, 6, 14)
        draw = ImageDraw.Draw(img)
        # Horizontal bands with interpolated color (fast enough at 440 lines)
        for y in range(h):
            t = y / max(h - 1, 1)
            c = _lerp_color(top_left, bot_right, t)
            draw.line([(0, y), (w, y)], fill=c)
        # Vignette: darken edges with semi-transparent overlay
        vig = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        vd = ImageDraw.Draw(vig)
        # Top edge
        for i in range(40):
            a = int(50 * (1.0 - i / 40))
            vd.line([(0, i), (w, i)], fill=(0, 0, 0, a))
        # Bottom edge
        for i in range(40):
            a = int(50 * (1.0 - i / 40))
            vd.line([(0, h - 1 - i), (w, h - 1 - i)], fill=(0, 0, 0, a))
        # Left edge
        for i in range(60):
            a = int(40 * (1.0 - i / 60))
            vd.line([(i, 0), (i, h)], fill=(0, 0, 0, a))
        # Right edge
        for i in range(60):
            a = int(40 * (1.0 - i / 60))
            vd.line([(w - 1 - i, 0), (w - 1 - i, h)], fill=(0, 0, 0, a))
        img.paste(Image.alpha_composite(Image.new('RGBA', (w, h), (0, 0, 0, 0)), vig), (0, 0), vig)
        _gradient_bg_cache[(w, h)] = img.copy()
    else:
        img.paste(_gradient_bg_cache[(w, h)], (0, 0))

# ── Panel drawing ──────────────────────────────────────────────────
def draw_panel(draw, img, x, y, w, h, title=""):
    """Draw a panel with gradient fill, glowing edges, and accent line."""
    paste_panel(draw, img, x, y, w, h, ACCENT, _lerp_color)
    if title:
        draw.text((x + 10, y + 6), title, fill=ACCENT, font=font(13))

# ── Glow bar ───────────────────────────────────────────────────────
def draw_glow_bar(draw, img, x, y, w, h, pct, color):
    """Draw a horizontal progress bar with dramatic glow halo."""
    draw.rectangle([x, y, x + w, y + h], fill=(12, 15, 22))

    fill_w = max(0, int(w * min(pct, 100) / 100))
    if fill_w <= 0:
        return

    glow, pad = glow_rect(fill_w, h, color, alpha=100, radius=10)
    if glow:
        img.paste(glow, (x - pad, y - pad), glow)

    # Gradient fill: dim at left, bright at right
    for col in range(fill_w):
        t = col / max(fill_w - 1, 1)
        c = _lerp_color(tuple(max(0, v - 80) for v in color), color, t * 0.8 + 0.2)
        draw.line([(x + 1 + col, y + 1), (x + 1 + col, y + h - 1)], fill=c)

    # Bright leading edge
    tip_w = min(5, fill_w)
    bright = tuple(min(255, c + 60) for c in color)
    draw.rectangle([x + fill_w - tip_w, y, x + fill_w, y + h], fill=bright)

    # Top highlight
    hl = tuple(min(255, c + 100) for c in color)
    draw.line([(x + 1, y + 1), (x + fill_w - 1, y + 1)], fill=hl)

def draw_arc_gauge(draw, img, cx, cy, radius, thickness, pct, color):
    """Draw a dramatic semi-circular arc gauge with glow."""
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]

    # Background arc
    draw.arc(bbox, 200, 340, fill=(25, 30, 45), width=thickness)

    # Filled arc with glow
    end_angle = 200 + int(140 * min(pct, 100) / 100)
    if pct > 0:
        # Cached atmospheric glow
        glow_img, gpad = glow_arc(radius, thickness, 200, end_angle, color,
                                   alpha=80, blur_radius=12, extra_width=16)
        img.paste(glow_img, (cx - radius - gpad, cy - radius - gpad), glow_img)
        # Cached core glow
        glow2, gpad2 = glow_arc(radius, thickness, 200, end_angle, color,
                                 alpha=160, blur_radius=5, extra_width=4)
        img.paste(glow2, (cx - radius - gpad2, cy - radius - gpad2), glow2)

        # Sharp filled arc
        draw.arc(bbox, 200, end_angle, fill=color, width=thickness)

        # Bright tip at the end
        import math
        angle_rad = math.radians(end_angle)
        tip_x = cx + int(radius * math.cos(angle_rad))
        tip_y = cy + int(radius * math.sin(angle_rad))
        bright = tuple(min(255, c + 80) for c in color)
        draw.ellipse([tip_x - 4, tip_y - 4, tip_x + 4, tip_y + 4], fill=bright)

    # Tick marks
    import math
    for i in range(0, 101, 25):
        angle = math.radians(200 + 140 * i / 100)
        ix = cx + int((radius + thickness // 2 + 4) * math.cos(angle))
        iy = cy + int((radius + thickness // 2 + 4) * math.sin(angle))
        ox = cx + int((radius + thickness // 2 + 8) * math.cos(angle))
        oy = cy + int((radius + thickness // 2 + 8) * math.sin(angle))
        draw.line([(ix, iy), (ox, oy)], fill=TEXT_DIM, width=1)

# ── Glow sparkline ─────────────────────────────────────────────────
def draw_sparkline(draw, img, x, y, w, h, data, color, max_val=None):
    """Draw a sparkline with gradient fill and glow effect."""
    if not data or len(data) < 2:
        # Empty background
        draw.rectangle([x, y, x + w, y + h], fill=(10, 13, 20))
        return
    draw.rectangle([x, y, x + w, y + h], fill=(10, 13, 20))
    mx = max_val or max(data) or 1
    points = []
    for i, val in enumerate(data):
        px = x + int(i * w / (len(data) - 1))
        py = y + h - int(min(val, mx) * h / mx)
        points.append((px, py))

    # Gradient fill under line: draw horizontal spans row by row
    # For performance, draw in bands of 2px
    dim_fill = tuple(max(0, c - 160) for c in color)
    fill_points = points + [(x + w, y + h), (x, y + h)]
    draw.polygon(fill_points, fill=dim_fill)
    # Lighter fill near the line (top portion)
    brighter = tuple(max(0, c - 120) for c in color)
    band_h = max(1, h // 5)
    # Draw a brighter polygon clipped to top band
    top_fill = []
    for px, py in points:
        top_fill.append((px, py))
    for px, py in reversed(points):
        top_fill.append((px, min(py + band_h, y + h)))
    draw.polygon(top_fill, fill=brighter)

    rel_points = [(px - x, py - y) for px, py in points]
    glow_img, gpad = glow_line(rel_points, w, h, color, alpha=140, width=6, radius=7)
    img.paste(glow_img, (x - gpad, y - gpad), glow_img)

    # Sharp bright line
    draw.line(points, fill=tuple(min(255, c + 40) for c in color), width=2)

# ── Glow core bars ─────────────────────────────────────────────────
def draw_core_bars(draw, img, x, y, w, h, core_pcts):
    """Draw vertical per-core bars with glowing caps."""
    n = len(core_pcts)
    if n == 0:
        return
    bar_w = max(3, (w - n + 1) // n)
    gap = 1
    total_w = n * bar_w + (n - 1) * gap
    offset = x + (w - total_w) // 2

    for i, pct in enumerate(core_pcts):
        bx = offset + i * (bar_w + gap)
        bar_h = max(0, int(h * min(pct, 100) / 100))
        # Background
        draw.rectangle([bx, y, bx + bar_w, y + h], fill=(14, 17, 26))
        if bar_h > 0:
            color = pct_color(pct)
            dim = tuple(max(0, c - 70) for c in color)
            draw.rectangle([bx, y + h - bar_h, bx + bar_w, y + h], fill=dim)
            # Bright cap (luminous top)
            cap_h = min(3, bar_h)
            bright = tuple(min(255, c + 40) for c in color)
            draw.rectangle([bx, y + h - bar_h, bx + bar_w, y + h - bar_h + cap_h], fill=bright)

# ── Hero text with glow ────────────────────────────────────────────
def draw_hero_text(draw, img, x, y, text, color, size):
    """Draw large text with cached glow."""
    f = font(size)
    paste_hero_text(draw, img, x, y, text, color, f)

# ── Main render ─────────────────────────────────────────────────────
def render_frame(w=1920, h=440):
    """Render one frame of the system monitor dashboard. Returns PIL Image."""
    # Collect data
    core_pcts = read_cpu()
    mem_used, mem_total, mem_pct = read_mem()
    temps = read_temps()
    gpu = read_gpu()
    rx_s, tx_s = read_net()
    load1, load5, load15 = read_load()
    uptime_str = read_uptime()
    disk_used, disk_total, disk_pct = read_disk()

    img = Image.new('RGBA', (w, h), BG + (255,))
    _draw_gradient_bg(img, w, h)
    draw = ImageDraw.Draw(img)

    # Background grid — visible but not distracting
    grid_color = (18, 24, 38, 50)
    for gx in range(0, w, 40):
        draw.line([(gx, 0), (gx, h)], fill=grid_color)
    for gy in range(0, h, 40):
        draw.line([(0, gy), (w, gy)], fill=grid_color)
    # Glowing dots at grid intersections
    for gx in range(0, w, 80):
        for gy in range(0, h, 80):
            draw.ellipse([gx - 2, gy - 2, gx + 2, gy + 2], fill=ACCENT + (25,))
            draw.ellipse([gx - 1, gy - 1, gx + 1, gy + 1], fill=ACCENT + (45,))

    # Bottom horizon glow — atmospheric depth
    horizon = Image.new('RGBA', (w, 60), (0, 0, 0, 0))
    hd = ImageDraw.Draw(horizon)
    for i in range(60):
        a = int(35 * (1.0 - i / 60))
        hd.line([(0, 59 - i), (w, 59 - i)], fill=(0, 100, 180, a))
    img.paste(horizon, (0, h - 60), horizon)

    pad = 8
    gap = 6
    py0 = pad                # panel top
    ph = h - pad * 2         # panel height
    bot = py0 + ph           # panel bottom

    # ═══════════════════════════════════════════════════════════════
    # Panel 1: CPU — arc gauge + core bars + sparkline
    # ═══════════════════════════════════════════════════════════════
    p1x, p1w = pad, 470
    draw_panel(draw, img, p1x, py0, p1w, ph, "CPU")

    model = read_cpu_model()
    short_model = model.replace('(R)', '').replace('(TM)', '').replace('CPU ', '').strip()[:34]
    draw.text((p1x + 10, py0 + 22), short_model, fill=TEXT_DIM, font=font(11))

    total_cpu = _cpu_history[-1] if _cpu_history else 0
    cpu_color = pct_color(total_cpu)

    # Arc gauge — left side of CPU panel
    gauge_r = 72
    gauge_cx = p1x + 10 + gauge_r + 10
    gauge_cy = py0 + 60 + gauge_r
    draw_arc_gauge(draw, img, gauge_cx, gauge_cy, gauge_r, 10, total_cpu, cpu_color)
    # Percentage inside the arc
    draw_hero_text(draw, img, gauge_cx - 38, gauge_cy - 30, f"{total_cpu:.0f}%",
                   cpu_color, 40)
    draw.text((gauge_cx - 18, gauge_cy + 4), "CPU", fill=TEXT_DIM, font=font(13))

    # Load averages — right of arc
    lx = gauge_cx + gauge_r + 20
    draw.text((lx, py0 + 40), "LOAD", fill=TEXT_DIM, font=font(11))
    draw.text((lx, py0 + 56), f"{load1:.1f}", fill=TEXT_BRIGHT, font=font(22))
    draw.text((lx, py0 + 82), f"{load5:.1f}  {load15:.1f}", fill=TEXT_DIM, font=font(14))

    # Per-core bars — right of arc, below load
    cores_y = py0 + 110
    cores_h = 90
    cores_x = lx
    cores_w = p1x + p1w - lx - 10
    draw_core_bars(draw, img, cores_x, cores_y, cores_w, cores_h, core_pcts)

    # CPU sparkline — full width at bottom
    spark_h = 70
    spark_y = bot - spark_h - 10
    draw_sparkline(draw, img, p1x + 10, spark_y, p1w - 20, spark_h,
                   _cpu_history, ACCENT, max_val=100)

    # ═══════════════════════════════════════════════════════════════
    # Panel 2: Memory + Disk + Swap (x=484, w=274)
    # ═══════════════════════════════════════════════════════════════
    p2x, p2w = p1x + p1w + gap, 274
    draw_panel(draw, img, p2x, py0, p2w, ph, "MEMORY")

    zone_h = (ph - 24) // 3

    # RAM zone
    ry = py0 + 24
    draw_hero_text(draw, img, p2x + 10, ry, f"{mem_pct:.0f}%",
                   pct_color(mem_pct), 44)
    draw.text((p2x + 105, ry + 16), f"{mem_used:.1f} / {mem_total:.1f} GB",
              fill=TEXT, font=font(15))
    draw_glow_bar(draw, img, p2x + 10, ry + 56, p2w - 20, 20, mem_pct, pct_color(mem_pct))

    # Disk zone
    dy = py0 + 24 + zone_h
    draw.text((p2x + 10, dy), "DISK /", fill=ACCENT, font=font(14))
    draw_hero_text(draw, img, p2x + 10, dy + 20, f"{disk_pct:.0f}%",
                   pct_color(disk_pct), 38)
    draw.text((p2x + 85, dy + 32), f"{disk_used:.0f} / {disk_total:.0f} GB",
              fill=TEXT, font=font(15))
    draw_glow_bar(draw, img, p2x + 10, dy + 64, p2w - 20, 20, disk_pct, pct_color(disk_pct))

    # Swap zone
    try:
        with open('/proc/meminfo') as f:
            mi = {}
            for line in f:
                parts = line.split()
                mi[parts[0].rstrip(':')] = int(parts[1])
        swap_total = mi.get('SwapTotal', 0)
        swap_free = mi.get('SwapFree', 0)
        swap_used = swap_total - swap_free
        if swap_total > 0:
            swap_pct = 100.0 * swap_used / swap_total
            sy = py0 + 24 + zone_h * 2
            draw.text((p2x + 10, sy), "SWAP", fill=ACCENT, font=font(14))
            draw_hero_text(draw, img, p2x + 10, sy + 20, f"{swap_pct:.0f}%",
                           pct_color(swap_pct), 32)
            draw.text((p2x + 72, sy + 28),
                      f"{swap_used//1024}M / {swap_total//1024}M", fill=TEXT_DIM, font=font(13))
            draw_glow_bar(draw, img, p2x + 10, sy + 56, p2w - 20, 16, swap_pct, PURPLE)
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════════
    # Panel 3: Temperatures (x=764, w=310)
    # ═══════════════════════════════════════════════════════════════
    p3x, p3w = p2x + p2w + gap, 310
    draw_panel(draw, img, p3x, py0, p3w, ph, "TEMPERATURES")

    # Package temp hero
    pkg_temp = temps.get('Package id 0', 0)
    draw_hero_text(draw, img, p3x + 10, py0 + 28, f"{pkg_temp:.0f}",
                   temp_color(pkg_temp), 68)
    draw.text((p3x + 100, py0 + 34), "°C", fill=temp_color(pkg_temp), font=font(26))
    draw.text((p3x + 100, py0 + 66), "Package", fill=TEXT_DIM, font=font(13))

    # Per-core temps
    core_temps = {k: v for k, v in temps.items() if k.startswith('Core')}
    n_ct = len(core_temps)
    if n_ct > 0:
        rows = (n_ct + 2) // 3
        row_h = min(55, (ph - 160) // max(rows + 1, 1))
        ty = py0 + 115
        col = 0
        for label, temp in sorted(core_temps.items()):
            cx = p3x + 10 + (col % 3) * 100
            cy = ty + (col // 3) * row_h
            draw.text((cx, cy), label, fill=TEXT_DIM, font=font(12))
            draw.text((cx, cy + 16), f"{temp:.0f}°C", fill=temp_color(temp), font=font(22))
            col += 1

    # PCH temp
    pch = temps.get('PCH', None)
    if pch:
        draw.text((p3x + 10, bot - 42), "PCH", fill=TEXT_DIM, font=font(12))
        draw.text((p3x + 10, bot - 24), f"{pch:.0f}°C", fill=temp_color(pch), font=font(20))

    # ═══════════════════════════════════════════════════════════════
    # Panel 4: GPU (x=1080, w=310)
    # ═══════════════════════════════════════════════════════════════
    p4x, p4w = p3x + p3w + gap, 310
    draw_panel(draw, img, p4x, py0, p4w, ph, "GPU")

    if gpu:
        gname = gpu['name'].replace('NVIDIA ', '').replace('GeForce ', '')
        draw.text((p4x + 10, py0 + 24), gname, fill=TEXT_DIM, font=font(14))

        # Temp hero
        draw_hero_text(draw, img, p4x + 10, py0 + 44, f"{gpu['temp']}°C",
                       temp_color(gpu['temp']), 50)

        # Utilization
        uy = py0 + 112
        draw.text((p4x + 10, uy), "Utilization", fill=TEXT_DIM, font=font(12))
        draw.text((p4x + 10, uy + 18), f"{gpu['util']}%",
                  fill=pct_color(gpu['util']), font=font(28))
        draw_glow_bar(draw, img, p4x + 10, uy + 54, p4w - 20, 18,
                      gpu['util'], pct_color(gpu['util']))

        # VRAM
        vram_pct = 100.0 * gpu['mem_used'] / gpu['mem_total'] if gpu['mem_total'] else 0
        vy = py0 + ph // 2 + 40
        draw.text((p4x + 10, vy), "VRAM", fill=TEXT_DIM, font=font(12))
        draw.text((p4x + 10, vy + 18), f"{gpu['mem_used']}M / {gpu['mem_total']}M",
                  fill=TEXT, font=font(20))
        draw_glow_bar(draw, img, p4x + 10, vy + 46, p4w - 20, 18, vram_pct, CYAN)

        # Power / Clock (hide if N/A) — cached, updated every 5s
        gpu_extra = _get_gpu_extra()
        if gpu_extra:
            power, clock = gpu_extra
            bx = p4x + 10
            if power and '[N/A]' not in power:
                draw.text((bx, bot - 38), f"{power}W", fill=YELLOW, font=font(16))
                bx += 90
            if clock and '[N/A]' not in clock:
                draw.text((bx, bot - 38), f"{clock} MHz", fill=TEXT, font=font(16))
    else:
        draw.text((p4x + 10, py0 + 44), "No GPU", fill=TEXT_DIM, font=font(18))
        draw.text((p4x + 10, py0 + 68), "detected", fill=TEXT_DIM, font=font(16))

    # ═══════════════════════════════════════════════════════════════
    # Panel 5: Network + System (x=1396, w=remaining)
    # ═══════════════════════════════════════════════════════════════
    p5x, p5w = p4x + p4w + gap, w - (p4x + p4w + gap) - pad
    draw_panel(draw, img, p5x, py0, p5w, ph, "NETWORK")

    # RX/TX rates with colored labels
    draw.text((p5x + 10, py0 + 26), "RX", fill=GREEN, font=font(15))
    draw.text((p5x + 40, py0 + 26), fmt_bytes(rx_s), fill=TEXT_BRIGHT, font=font(16))
    draw.text((p5x + 10, py0 + 48), "TX", fill=ORANGE, font=font(15))
    draw.text((p5x + 40, py0 + 48), fmt_bytes(tx_s), fill=TEXT_BRIGHT, font=font(16))

    # Network sparklines
    max_net = max(max(_net_rx_history, default=1), max(_net_tx_history, default=1), 1)
    spark_w = p5w - 20
    net_spark_h = 60
    draw_sparkline(draw, img, p5x + 10, py0 + 72, spark_w, net_spark_h,
                   _net_rx_history, GREEN, max_val=max_net)
    draw_sparkline(draw, img, p5x + 10, py0 + 72 + net_spark_h + 6, spark_w, net_spark_h,
                   _net_tx_history, ORANGE, max_val=max_net)

    # System info section
    sy = py0 + 72 + net_spark_h * 2 + 16
    # Thin accent divider with glow
    div_accent = glow_accent_line(p5w - 16, ACCENT, alpha=120, radius=2)
    img.paste(div_accent, (p5x + 8, sy - 2), div_accent)

    draw.text((p5x + 10, sy + 6), "SYSTEM", fill=ACCENT, font=font(13))

    hostname = read_hostname()
    draw_hero_text(draw, img, p5x + 10, sy + 26, hostname, TEXT_BRIGHT, 26)

    info_y = sy + 58
    row_gap = 22
    draw.text((p5x + 10, info_y), "Uptime", fill=TEXT_DIM, font=font(12))
    draw.text((p5x + 72, info_y), uptime_str, fill=TEXT, font=font(14))

    primary_ip = _get_ip()
    draw.text((p5x + 10, info_y + row_gap), "IP", fill=TEXT_DIM, font=font(12))
    draw.text((p5x + 72, info_y + row_gap), primary_ip, fill=TEXT, font=font(14))

    draw.text((p5x + 10, info_y + row_gap * 2), "Time", fill=TEXT_DIM, font=font(12))
    draw.text((p5x + 72, info_y + row_gap * 2), time.strftime("%Y-%m-%d %H:%M:%S"),
              fill=TEXT, font=font(14))

    # Bottom accent line with glow
    bottom_accent = glow_accent_line(w, ACCENT, alpha=160, radius=4)
    img.paste(bottom_accent, (0, h - 12), bottom_accent)

    # Scanline overlay — subtle CRT/HUD texture
    scanlines = _get_scanlines(w, h)
    img = Image.alpha_composite(img, scanlines)

    # Convert RGBA to RGB for output
    out = Image.new('RGB', (w, h), BG)
    out.paste(img, (0, 0), img)
    return out


# ── Standalone mode ─────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    if '--once' in sys.argv:
        # Warm up CPU readings
        read_cpu()
        read_net()
        time.sleep(1)
        img = render_frame()
        img.save('/tmp/sysmon.png')
        print("Saved to /tmp/sysmon.png")
    elif '--loop' in sys.argv:
        # Direct USB output mode (for use without tinyscreen wrapper)
        import struct as st
        from io import BytesIO

        sys.path.insert(0, os.path.dirname(__file__))
        # Import tinyscreen auth functions
        from tinyscreen import (find_device, setup_device, get_params,
                                authenticate, send_frame, image_to_jpeg, log)

        dev = find_device()
        if not dev:
            print("Display not found")
            sys.exit(1)
        setup_device(dev)
        w, h, fmt, fps = get_params(dev)
        if not authenticate(dev):
            print("Auth failed")
            sys.exit(1)

        print(f"Streaming sysmon to {w}x{h} display...")
        read_cpu()
        read_net()
        time.sleep(0.5)

        fid = 0
        while True:
            img = render_frame(w, h)
            jpeg = image_to_jpeg(img, 80)
            try:
                send_frame(dev, jpeg, fmt, fid)
            except Exception as e:
                print(f"USB error: {e}")
                break
            fid += 1
            if fid % 30 == 0:
                print(f"Frame {fid}, {len(jpeg)//1024}KB")
            time.sleep(1)
    else:
        print("Usage:")
        print("  python3 sysmon.py --once   # save one frame to /tmp/sysmon.png")
        print("  python3 sysmon.py --loop   # stream to display (needs root)")
        print("  tinyscreen --sysmon        # recommended: run via tinyscreen")
