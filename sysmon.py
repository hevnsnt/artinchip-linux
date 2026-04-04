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
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── Colors ──────────────────────────────────────────────────────────
BG          = (8, 10, 16)
PANEL_BG    = (14, 18, 28)
ACCENT      = (0, 180, 255)
ACCENT_DIM  = (0, 60, 120)
TEXT        = (210, 215, 230)
TEXT_DIM    = (80, 90, 110)
TEXT_BRIGHT = (245, 248, 255)
GREEN       = (0, 230, 120)
YELLOW      = (255, 210, 0)
RED         = (255, 70, 70)
ORANGE      = (255, 150, 30)
CYAN        = (0, 220, 240)
PURPLE      = (140, 100, 255)

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
def _draw_gradient_bg(img, w, h):
    """Draw a smooth diagonal gradient background with vignette."""
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

# ── Panel drawing ──────────────────────────────────────────────────
def draw_panel(draw, img, x, y, w, h, title=""):
    """Draw a panel with subtle gradient fill and glowing accent line."""
    # Panel body: subtle vertical gradient
    top_c = (16, 20, 32)
    bot_c = (12, 15, 24)
    for row in range(h):
        t = row / max(h - 1, 1)
        c = _lerp_color(top_c, bot_c, t)
        draw.line([(x, y + row), (x + w, y + row)], fill=c)
    # Glowing accent line at top
    pad = 8
    glow_w = w - pad * 2
    glow_h = 14
    glow = Image.new('RGBA', (glow_w, glow_h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rectangle([0, 0, glow_w, 2], fill=ACCENT + (180,))
    gd.rectangle([0, 2, glow_w, 4], fill=ACCENT + (50,))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=3))
    img.paste(glow, (x + pad, y), glow)
    if title:
        draw.text((x + 10, y + 6), title, fill=ACCENT, font=font(13))

# ── Glow bar ───────────────────────────────────────────────────────
def draw_glow_bar(draw, img, x, y, w, h, pct, color):
    """Draw a horizontal progress bar with soft glow halo."""
    # Bar background (dark inset)
    draw.rectangle([x, y, x + w, y + h], fill=(12, 15, 22))

    fill_w = max(0, int(w * min(pct, 100) / 100))
    if fill_w <= 0:
        return

    # Glow layer (small, cropped around bar region)
    pad = 10
    gw, gh = fill_w + pad * 2, h + pad * 2
    if gw < 1 or gh < 1:
        return
    glow = Image.new('RGBA', (gw, gh), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rectangle([pad, pad, pad + fill_w, pad + h], fill=color + (70,))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=6))
    gx, gy = x - pad, y - pad
    img.paste(glow, (gx, gy), glow)

    # Filled bar body (slightly dimmed)
    dim = tuple(max(0, c - 50) for c in color)
    draw.rectangle([x + 1, y + 1, x + fill_w - 1, y + h - 1], fill=dim)

    # Bright leading edge
    tip_w = min(4, fill_w)
    draw.rectangle([x + fill_w - tip_w, y + 1, x + fill_w, y + h - 1], fill=color)

    # Soft highlight line on top edge of fill
    hl = tuple(min(255, c + 80) for c in color)
    draw.line([(x + 1, y + 1), (x + fill_w - 1, y + 1)], fill=hl)

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

    # Glow on the line itself (small cropped region)
    pad = 6
    gw, gh = w + pad * 2, h + pad * 2
    glow = Image.new('RGBA', (gw, gh), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    shifted = [(px - x + pad, py - y + pad) for px, py in points]
    gd.line(shifted, fill=color + (100,), width=4)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=4))
    img.paste(glow, (x - pad, y - pad), glow)

    # Sharp line on top
    draw.line(points, fill=color, width=2)

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

    # Collect glow regions and batch them onto one glow layer
    glow_pad = 6
    region_x0 = offset - glow_pad
    region_x1 = offset + total_w + glow_pad
    region_y0 = y - glow_pad
    region_y1 = y + h + glow_pad
    rw = max(1, region_x1 - region_x0)
    rh = max(1, region_y1 - region_y0)
    glow = Image.new('RGBA', (rw, rh), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)

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
            # Add glow cap to glow layer
            lx = bx - region_x0
            ly = (y + h - bar_h) - region_y0
            gd.rectangle([lx - 1, ly - 2, lx + bar_w + 1, ly + cap_h + 2],
                         fill=color + (60,))

    glow = glow.filter(ImageFilter.GaussianBlur(radius=4))
    img.paste(glow, (region_x0, region_y0), glow)

# ── Hero text with glow ────────────────────────────────────────────
def draw_hero_text(draw, img, x, y, text, color, size):
    """Draw large text with a subtle glow behind it."""
    f = font(size)
    # Measure text for glow region
    bbox = f.getbbox(text)
    tw = bbox[2] - bbox[0] + 20
    th = bbox[3] - bbox[1] + 20
    pad = 8
    glow = Image.new('RGBA', (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.text((pad - bbox[0], pad - bbox[1]), text, fill=color + (90,), font=f)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=5))
    img.paste(glow, (x - pad, y - pad), glow)
    # Sharp text on top
    draw.text((x, y), text, fill=color, font=f)

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

    pad = 8
    gap = 6
    py0 = pad                # panel top
    ph = h - pad * 2         # panel height
    bot = py0 + ph           # panel bottom

    # ═══════════════════════════════════════════════════════════════
    # Panel 1: CPU (x=8, w=470)
    # ═══════════════════════════════════════════════════════════════
    p1x, p1w = pad, 470
    draw_panel(draw, img, p1x, py0, p1w, ph, "CPU")

    model = read_cpu_model()
    short_model = model.replace('(R)', '').replace('(TM)', '').replace('CPU ', '').strip()[:34]
    draw.text((p1x + 10, py0 + 22), short_model, fill=TEXT_DIM, font=font(12))

    total_cpu = _cpu_history[-1] if _cpu_history else 0
    draw_hero_text(draw, img, p1x + 10, py0 + 38, f"{total_cpu:.0f}%",
                   pct_color(total_cpu), 52)
    draw.text((p1x + 150, py0 + 42), "Load", fill=TEXT_DIM, font=font(11))
    draw.text((p1x + 150, py0 + 58), f"{load1:.1f}  {load5:.1f}  {load15:.1f}",
              fill=TEXT, font=font(14))

    # Per-core bars
    cores_y = py0 + 100
    cores_h = ph - 200
    draw_core_bars(draw, img, p1x + 10, cores_y, p1w - 20, cores_h, core_pcts)

    # Core labels
    n_cores = len(core_pcts)
    if n_cores > 0:
        bar_w = max(3, (p1w - 20 - n_cores + 1) // n_cores)
        total_bw = n_cores * bar_w + (n_cores - 1)
        offset = p1x + 10 + (p1w - 20 - total_bw) // 2
        for i in range(n_cores):
            bx = offset + i * (bar_w + 1)
            if n_cores <= 12 or i % 2 == 0:
                draw.text((bx, cores_y + cores_h + 3), str(i), fill=TEXT_DIM, font=font(9))

    # CPU sparkline
    spark_h = 80
    spark_y = bot - spark_h - 10
    draw.text((p1x + 10, spark_y - 2), "60s", fill=TEXT_DIM, font=font(10))
    draw_sparkline(draw, img, p1x + 34, spark_y, p1w - 44, spark_h,
                   _cpu_history, ACCENT, max_val=100)
    if _cpu_history:
        draw.text((p1x + p1w - 52, spark_y + 4), f"{_cpu_history[-1]:.0f}%",
                  fill=pct_color(_cpu_history[-1]), font=font(13))

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

        # Power / Clock
        try:
            out = subprocess.run(
                ['nvidia-smi', '--query-gpu=power.draw,clocks.current.graphics',
                 '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=2)
            if out.returncode == 0:
                parts = out.stdout.strip().split(', ')
                power = parts[0].strip()
                clock = parts[1].strip()
                draw.text((p4x + 10, bot - 38), f"{power}W", fill=YELLOW, font=font(16))
                draw.text((p4x + 100, bot - 38), f"{clock} MHz", fill=TEXT, font=font(16))
        except Exception:
            pass
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
    div_glow = Image.new('RGBA', (p5w - 16, 8), (0, 0, 0, 0))
    dgd = ImageDraw.Draw(div_glow)
    dgd.rectangle([0, 3, p5w - 16, 4], fill=ACCENT + (120,))
    div_glow = div_glow.filter(ImageFilter.GaussianBlur(radius=2))
    img.paste(div_glow, (p5x + 8, sy - 2), div_glow)

    draw.text((p5x + 10, sy + 6), "SYSTEM", fill=ACCENT, font=font(13))

    hostname = read_hostname()
    draw_hero_text(draw, img, p5x + 10, sy + 26, hostname, TEXT_BRIGHT, 26)

    info_y = sy + 58
    row_gap = 22
    draw.text((p5x + 10, info_y), "Uptime", fill=TEXT_DIM, font=font(12))
    draw.text((p5x + 72, info_y), uptime_str, fill=TEXT, font=font(14))

    try:
        out = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=2)
        ips = out.stdout.strip().split()
        primary_ip = ips[0] if ips else '--'
    except Exception:
        primary_ip = '--'
    draw.text((p5x + 10, info_y + row_gap), "IP", fill=TEXT_DIM, font=font(12))
    draw.text((p5x + 72, info_y + row_gap), primary_ip, fill=TEXT, font=font(14))

    draw.text((p5x + 10, info_y + row_gap * 2), "Time", fill=TEXT_DIM, font=font(12))
    draw.text((p5x + 72, info_y + row_gap * 2), time.strftime("%Y-%m-%d %H:%M:%S"),
              fill=TEXT, font=font(14))

    # Bottom accent glow line
    bottom_glow = Image.new('RGBA', (w, 10), (0, 0, 0, 0))
    bgd = ImageDraw.Draw(bottom_glow)
    bgd.rectangle([0, 4, w, 6], fill=ACCENT + (100,))
    bottom_glow = bottom_glow.filter(ImageFilter.GaussianBlur(radius=3))
    img.paste(bottom_glow, (0, h - 8), bottom_glow)
    draw.rectangle([0, h - 2, w, h], fill=ACCENT_DIM)

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
