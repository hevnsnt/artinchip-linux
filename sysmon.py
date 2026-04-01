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

# ── Colors ──────────────────────────────────────────────────────────
BG         = (10, 12, 18)
PANEL_BG   = (18, 22, 32)
BORDER     = (35, 42, 58)
ACCENT     = (0, 170, 255)
ACCENT_DIM = (0, 80, 140)
TEXT       = (200, 210, 225)
TEXT_DIM   = (100, 110, 130)
TEXT_BRIGHT= (240, 245, 255)
GREEN      = (0, 220, 100)
YELLOW     = (255, 200, 0)
RED        = (255, 60, 60)
ORANGE     = (255, 140, 0)
CYAN       = (0, 220, 220)
PURPLE     = (160, 100, 255)

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

def draw_bar(draw, x, y, w, h, pct, color, bg=PANEL_BG):
    """Draw a horizontal progress bar."""
    draw.rectangle([x, y, x + w, y + h], fill=bg, outline=BORDER)
    fill_w = max(0, int(w * min(pct, 100) / 100))
    if fill_w > 0:
        # Gradient effect: dim version for the bar body
        dim = tuple(max(0, c - 60) for c in color)
        draw.rectangle([x + 1, y + 1, x + fill_w, y + h - 1], fill=dim)
        # Bright tip
        tip_w = min(3, fill_w)
        draw.rectangle([x + fill_w - tip_w, y + 1, x + fill_w, y + h - 1], fill=color)

def draw_sparkline(draw, x, y, w, h, data, color, max_val=None):
    """Draw a mini sparkline graph."""
    if not data or len(data) < 2:
        return
    draw.rectangle([x, y, x + w, y + h], fill=(12, 14, 22))
    mx = max_val or max(data) or 1
    points = []
    for i, val in enumerate(data):
        px = x + int(i * w / (len(data) - 1))
        py = y + h - int(min(val, mx) * h / mx)
        points.append((px, py))
    # Fill under the line
    fill_points = points + [(x + w, y + h), (x, y + h)]
    fill_color = tuple(max(0, c - 180) for c in color)
    draw.polygon(fill_points, fill=fill_color)
    # Draw the line
    draw.line(points, fill=color, width=2)

def draw_core_bars(draw, x, y, w, h, core_pcts):
    """Draw vertical bars for each CPU core."""
    n = len(core_pcts)
    if n == 0:
        return
    bar_w = max(2, (w - n + 1) // n)
    gap = 1
    total_w = n * bar_w + (n - 1) * gap
    offset = x + (w - total_w) // 2
    for i, pct in enumerate(core_pcts):
        bx = offset + i * (bar_w + gap)
        bar_h = max(0, int(h * min(pct, 100) / 100))
        # Background
        draw.rectangle([bx, y, bx + bar_w, y + h], fill=(20, 24, 35))
        # Filled portion
        if bar_h > 0:
            color = pct_color(pct)
            dim = tuple(max(0, c - 80) for c in color)
            draw.rectangle([bx, y + h - bar_h, bx + bar_w, y + h], fill=dim)
            # Bright cap
            cap_h = min(2, bar_h)
            draw.rectangle([bx, y + h - bar_h, bx + bar_w, y + h - bar_h + cap_h], fill=color)

def draw_panel(draw, x, y, w, h, title=""):
    """Draw a panel background with optional title."""
    draw.rectangle([x, y, x + w, y + h], fill=PANEL_BG, outline=BORDER)
    # Top accent line
    draw.rectangle([x, y, x + w, y + 1], fill=ACCENT_DIM)
    if title:
        draw.text((x + 8, y + 4), title, fill=ACCENT, font=font(13))

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

    img = Image.new('RGB', (w, h), BG)
    draw = ImageDraw.Draw(img)

    # Layout: 5 panels across the 1920px width
    # [CPU 480px] [MEM+DISK 280px] [TEMPS 320px] [GPU 320px] [NET+SYS 480px]
    pad = 6
    panel_y = pad
    panel_h = h - pad * 2

    # ═══════════════════════════════════════════════════════════════
    # Panel 1: CPU (x=6, w=470)
    # ═══════════════════════════════════════════════════════════════
    p1x, p1w = pad, 470
    draw_panel(draw, p1x, panel_y, p1w, panel_h, "CPU")

    # CPU model (shortened)
    model = read_cpu_model()
    short_model = model.replace('(R)', '').replace('(TM)', '').replace('CPU ', '').strip()
    if len(short_model) > 30:
        short_model = short_model[:30]
    draw.text((p1x + 8, panel_y + 20), short_model, fill=TEXT_DIM, font=font(12))

    # Total CPU % large
    total_cpu = _cpu_history[-1] if _cpu_history else 0
    cpu_str = f"{total_cpu:.0f}%"
    draw.text((p1x + 8, panel_y + 36), cpu_str, fill=pct_color(total_cpu), font=font(42))

    # Load averages
    draw.text((p1x + 130, panel_y + 42), f"Load", fill=TEXT_DIM, font=font(11))
    draw.text((p1x + 130, panel_y + 56), f"{load1:.1f}  {load5:.1f}  {load15:.1f}", fill=TEXT, font=font(13))

    # Per-core vertical bars
    cores_x = p1x + 10
    cores_y = panel_y + 90
    cores_w = p1w - 20
    cores_h = 120
    draw_core_bars(draw, cores_x, cores_y, cores_w, cores_h, core_pcts)

    # Core labels
    n_cores = len(core_pcts)
    if n_cores > 0:
        bar_w = max(2, (cores_w - n_cores + 1) // n_cores)
        gap = 1
        total_bw = n_cores * bar_w + (n_cores - 1) * gap
        offset = cores_x + (cores_w - total_bw) // 2
        for i in range(n_cores):
            bx = offset + i * (bar_w + gap)
            if n_cores <= 12 or i % 2 == 0:
                draw.text((bx, cores_y + cores_h + 2), str(i), fill=TEXT_DIM, font=font(9))

    # CPU sparkline
    spark_y = cores_y + cores_h + 16
    draw.text((p1x + 8, spark_y), "60s", fill=TEXT_DIM, font=font(10))
    draw_sparkline(draw, p1x + 30, spark_y, p1w - 40, 60, _cpu_history, ACCENT, max_val=100)
    # Current value at right end of sparkline
    if _cpu_history:
        draw.text((p1x + p1w - 45, spark_y + 2), f"{_cpu_history[-1]:.0f}%",
                  fill=pct_color(_cpu_history[-1]), font=font(12))

    # ═══════════════════════════════════════════════════════════════
    # Panel 2: Memory + Disk (x=482, w=270)
    # ═══════════════════════════════════════════════════════════════
    p2x, p2w = p1x + p1w + pad, 270
    draw_panel(draw, p2x, panel_y, p2w, panel_h, "MEMORY")

    # RAM
    draw.text((p2x + 8, panel_y + 22), f"{mem_pct:.0f}%", fill=pct_color(mem_pct), font=font(36))
    draw.text((p2x + 90, panel_y + 36), f"{mem_used:.1f} / {mem_total:.1f} GB",
              fill=TEXT, font=font(14))

    draw_bar(draw, p2x + 8, panel_y + 68, p2w - 16, 18, mem_pct, pct_color(mem_pct))

    # Disk
    draw.text((p2x + 8, panel_y + 100), "DISK /", fill=ACCENT, font=font(13))
    draw.text((p2x + 8, panel_y + 118), f"{disk_pct:.0f}%", fill=pct_color(disk_pct), font=font(30))
    draw.text((p2x + 70, panel_y + 128), f"{disk_used:.0f} / {disk_total:.0f} GB",
              fill=TEXT, font=font(14))

    draw_bar(draw, p2x + 8, panel_y + 158, p2w - 16, 18, disk_pct, pct_color(disk_pct))

    # Swap
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
            draw.text((p2x + 8, panel_y + 190), "SWAP", fill=ACCENT, font=font(13))
            draw.text((p2x + 60, panel_y + 190),
                      f"{swap_used//1024}M / {swap_total//1024}M  ({swap_pct:.0f}%)",
                      fill=TEXT_DIM, font=font(12))
            draw_bar(draw, p2x + 8, panel_y + 208, p2w - 16, 12, swap_pct, PURPLE)
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════════
    # Panel 3: Temperatures (x=758, w=310)
    # ═══════════════════════════════════════════════════════════════
    p3x, p3w = p2x + p2w + pad, 310
    draw_panel(draw, p3x, panel_y, p3w, panel_h, "TEMPERATURES")

    ty = panel_y + 24
    # Package temp big
    pkg_temp = temps.get('Package id 0', 0)
    draw.text((p3x + 8, ty), f"{pkg_temp:.0f}", fill=temp_color(pkg_temp), font=font(52))
    draw.text((p3x + 75, ty + 6), "°C", fill=temp_color(pkg_temp), font=font(20))
    draw.text((p3x + 75, ty + 32), "Package", fill=TEXT_DIM, font=font(12))

    # Per-core temps as a grid
    core_temps = {k: v for k, v in temps.items() if k.startswith('Core')}
    ty += 70
    col = 0
    for label, temp in sorted(core_temps.items()):
        cx = p3x + 8 + (col % 3) * 100
        cy = ty + (col // 3) * 40
        draw.text((cx, cy), label, fill=TEXT_DIM, font=font(11))
        draw.text((cx, cy + 14), f"{temp:.0f}°C", fill=temp_color(temp), font=font(18))
        col += 1

    # PCH temp
    pch = temps.get('PCH', None)
    if pch:
        py = ty + ((col + 2) // 3) * 40 + 8
        draw.text((p3x + 8, py), "PCH", fill=TEXT_DIM, font=font(11))
        draw.text((p3x + 8, py + 14), f"{pch:.0f}°C", fill=temp_color(pch), font=font(18))

    # ═══════════════════════════════════════════════════════════════
    # Panel 4: GPU (x=1074, w=310)
    # ═══════════════════════════════════════════════════════════════
    p4x, p4w = p3x + p3w + pad, 310
    draw_panel(draw, p4x, panel_y, p4w, panel_h, "GPU")

    if gpu:
        # GPU name
        gname = gpu['name'].replace('NVIDIA ', '').replace('GeForce ', '')
        draw.text((p4x + 8, panel_y + 22), gname, fill=TEXT_DIM, font=font(13))

        # Temperature big
        draw.text((p4x + 8, panel_y + 42), f"{gpu['temp']}°C",
                  fill=temp_color(gpu['temp']), font=font(38))

        # Utilization
        draw.text((p4x + 8, panel_y + 92), "Utilization", fill=TEXT_DIM, font=font(11))
        draw.text((p4x + 8, panel_y + 106), f"{gpu['util']}%",
                  fill=pct_color(gpu['util']), font=font(22))
        draw_bar(draw, p4x + 8, panel_y + 134, p4w - 16, 14, gpu['util'], pct_color(gpu['util']))

        # VRAM
        vram_pct = 100.0 * gpu['mem_used'] / gpu['mem_total'] if gpu['mem_total'] else 0
        draw.text((p4x + 8, panel_y + 160), "VRAM", fill=TEXT_DIM, font=font(11))
        draw.text((p4x + 8, panel_y + 174), f"{gpu['mem_used']}M / {gpu['mem_total']}M",
                  fill=TEXT, font=font(16))
        draw_bar(draw, p4x + 8, panel_y + 198, p4w - 16, 14, vram_pct, CYAN)
    else:
        draw.text((p4x + 8, panel_y + 30), "No GPU detected", fill=TEXT_DIM, font=font(14))

    # ═══════════════════════════════════════════════════════════════
    # Panel 5: Network + System (x=1390, w=524)
    # ═══════════════════════════════════════════════════════════════
    p5x, p5w = p4x + p4w + pad, w - (p4x + p4w + pad) - pad
    draw_panel(draw, p5x, panel_y, p5w, panel_h, "NETWORK")

    # RX/TX rates
    draw.text((p5x + 8, panel_y + 22), "RX", fill=GREEN, font=font(13))
    draw.text((p5x + 35, panel_y + 22), fmt_bytes(rx_s), fill=TEXT, font=font(15))
    draw.text((p5x + 8, panel_y + 42), "TX", fill=ORANGE, font=font(13))
    draw.text((p5x + 35, panel_y + 42), fmt_bytes(tx_s), fill=TEXT, font=font(15))

    # Network sparklines
    max_net = max(max(_net_rx_history, default=1), max(_net_tx_history, default=1), 1)
    spark_w = p5w - 16
    draw_sparkline(draw, p5x + 8, panel_y + 66, spark_w, 50, _net_rx_history, GREEN, max_val=max_net)
    draw_sparkline(draw, p5x + 8, panel_y + 120, spark_w, 50, _net_tx_history, ORANGE, max_val=max_net)

    # System info section
    sy = panel_y + 180
    draw.rectangle([p5x, sy, p5x + p5w, sy + 1], fill=BORDER)
    draw.text((p5x + 8, sy + 4), "SYSTEM", fill=ACCENT, font=font(13))

    hostname = read_hostname()
    draw.text((p5x + 8, sy + 24), hostname, fill=TEXT_BRIGHT, font=font(22))

    # Uptime
    draw.text((p5x + 8, sy + 52), "Uptime", fill=TEXT_DIM, font=font(11))
    draw.text((p5x + 60, sy + 52), uptime_str, fill=TEXT, font=font(13))

    # IP
    try:
        out = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=2)
        ips = out.stdout.strip().split()
        primary_ip = ips[0] if ips else '—'
    except Exception:
        primary_ip = '—'
    draw.text((p5x + 8, sy + 72), "IP", fill=TEXT_DIM, font=font(11))
    draw.text((p5x + 60, sy + 72), primary_ip, fill=TEXT, font=font(13))

    # Time
    draw.text((p5x + 8, sy + 92), "Time", fill=TEXT_DIM, font=font(11))
    draw.text((p5x + 60, sy + 92), time.strftime("%Y-%m-%d %H:%M:%S"), fill=TEXT, font=font(13))

    # Bottom accent line
    draw.rectangle([0, h - 2, w, h], fill=ACCENT_DIM)

    return img


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
