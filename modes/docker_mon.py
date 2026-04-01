"""Docker container status monitor for tinyscreen bar display."""

import subprocess
import time
from PIL import Image, ImageDraw, ImageFont

# --- Color palette (dark theme) ---
BG = (10, 12, 18)
PANEL_BG = (18, 22, 32)
BORDER = (35, 42, 58)
ACCENT = (0, 170, 255)
TEXT = (200, 210, 225)
TEXT_DIM = (100, 110, 130)
TEXT_BRIGHT = (240, 245, 255)
GREEN = (0, 220, 100)
RED = (255, 60, 60)
YELLOW = (255, 200, 0)
ORANGE = (255, 140, 0)
CYAN = (0, 220, 220)
PURPLE = (160, 100, 255)

# --- Font cache ---
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

# --- Data cache ---
_cache = {
    'stats': [],
    'ps': {},
    'last_fetch': 0,
    'error': None,
}

REFRESH_INTERVAL = 3  # seconds


def _fetch_docker_data():
    """Fetch docker stats and ps data via subprocess."""
    now = time.time()
    if now - _cache['last_fetch'] < REFRESH_INTERVAL:
        return

    _cache['last_fetch'] = now

    # Fetch docker ps info (name -> status, image)
    try:
        ps_result = subprocess.run(
            ['docker', 'ps', '-a', '--format', '{{.Names}}\t{{.Status}}\t{{.Image}}'],
            capture_output=True, text=True, timeout=5
        )
        ps_map = {}
        for line in ps_result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split('\t')
            if len(parts) >= 3:
                ps_map[parts[0]] = {'status': parts[1], 'image': parts[2]}
            elif len(parts) == 2:
                ps_map[parts[0]] = {'status': parts[1], 'image': ''}
        _cache['ps'] = ps_map
    except Exception as e:
        _cache['ps'] = {}
        _cache['error'] = str(e)

    # Fetch docker stats
    try:
        stats_result = subprocess.run(
            ['docker', 'stats', '--no-stream', '--format',
             '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.PIDs}}'],
            capture_output=True, text=True, timeout=10
        )
        if stats_result.returncode != 0:
            _cache['stats'] = []
            _cache['error'] = stats_result.stderr.strip() or 'docker stats failed'
            return

        containers = []
        for line in stats_result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split('\t')
            if len(parts) < 6:
                continue
            name = parts[0]
            cpu_str = parts[1].replace('%', '').strip()
            mem_usage = parts[2].strip()
            mem_pct_str = parts[3].replace('%', '').strip()
            net_io = parts[4].strip()
            pids = parts[5].strip()

            try:
                cpu_val = float(cpu_str)
            except ValueError:
                cpu_val = 0.0
            try:
                mem_val = float(mem_pct_str)
            except ValueError:
                mem_val = 0.0

            ps_info = _cache['ps'].get(name, {})
            status_text = ps_info.get('status', 'Unknown')
            running = status_text.lower().startswith('up')

            containers.append({
                'name': name,
                'cpu': cpu_val,
                'cpu_str': parts[1].strip(),
                'mem_usage': mem_usage,
                'mem_pct': mem_val,
                'mem_str': parts[3].strip(),
                'net_io': net_io,
                'pids': pids,
                'running': running,
                'status_text': status_text,
            })

        # Sort by CPU descending
        containers.sort(key=lambda c: c['cpu'], reverse=True)
        _cache['stats'] = containers
        _cache['error'] = None

    except FileNotFoundError:
        _cache['stats'] = []
        _cache['error'] = 'Docker not found'
    except subprocess.TimeoutExpired:
        _cache['error'] = 'Docker command timed out'
    except Exception as e:
        _cache['stats'] = []
        _cache['error'] = str(e)


def _draw_bar(draw, x, y, w, h, pct, color, bg_color=BORDER):
    """Draw a horizontal percentage bar."""
    draw.rounded_rectangle([x, y, x + w, y + h], radius=3, fill=bg_color)
    fill_w = max(0, int(w * min(pct, 100.0) / 100.0))
    if fill_w > 0:
        draw.rounded_rectangle([x, y, x + fill_w, y + h], radius=3, fill=color)


def render_frame(w, h):
    """Render one frame of the docker monitor. Returns a PIL Image."""
    img = Image.new('RGB', (w, h), BG)
    draw = ImageDraw.Draw(img)

    _fetch_docker_data()

    pad_x = 12
    pad_y = 6

    # Title / header area
    title_font = font(22)
    label_font = font(16)
    row_font = font(18)
    small_font = font(14)

    header_h = 42
    draw.rectangle([0, 0, w, header_h], fill=PANEL_BG)
    draw.line([0, header_h, w, header_h], fill=BORDER, width=1)

    draw.text((pad_x, 10), "DOCKER CONTAINERS", fill=ACCENT, font=title_font)

    containers = _cache['stats']
    count_text = f"{len(containers)} running"
    if _cache['ps']:
        total = len(_cache['ps'])
        if total != len(containers):
            count_text = f"{len(containers)} active / {total} total"
    draw.text((w - pad_x - label_font.getlength(count_text), 14), count_text,
              fill=TEXT_DIM, font=label_font)

    # Handle empty / error states
    if _cache['error'] and not containers:
        msg = _cache['error']
        if 'not found' in msg.lower():
            msg = "Docker is not available"
        elif 'timed out' in msg.lower():
            msg = "Docker command timed out"
        msg_font = font(24)
        tw = msg_font.getlength(msg)
        draw.text(((w - tw) / 2, (h - 24) / 2), msg, fill=TEXT_DIM, font=msg_font)
        return img

    if not containers:
        msg = "No containers running"
        msg_font = font(24)
        tw = msg_font.getlength(msg)
        draw.text(((w - tw) / 2, (h - 24) / 2), msg, fill=TEXT_DIM, font=msg_font)
        return img

    # Column labels row
    col_label_y = header_h + 4
    col_label_h = 22
    draw.rectangle([0, col_label_y, w, col_label_y + col_label_h], fill=PANEL_BG)

    # Define column positions
    col_status_x = pad_x
    col_name_x = pad_x + 20
    col_cpu_x = 300
    col_mem_x = 620
    col_net_x = 940
    col_pids_x = 1250

    draw.text((col_status_x, col_label_y + 2), " ", fill=TEXT_DIM, font=small_font)
    draw.text((col_name_x, col_label_y + 2), "CONTAINER", fill=TEXT_DIM, font=small_font)
    draw.text((col_cpu_x, col_label_y + 2), "CPU", fill=TEXT_DIM, font=small_font)
    draw.text((col_mem_x, col_label_y + 2), "MEMORY", fill=TEXT_DIM, font=small_font)
    draw.text((col_net_x, col_label_y + 2), "NET I/O", fill=TEXT_DIM, font=small_font)
    draw.text((col_pids_x, col_label_y + 2), "PIDs", fill=TEXT_DIM, font=small_font)

    draw.line([0, col_label_y + col_label_h, w, col_label_y + col_label_h],
              fill=BORDER, width=1)

    # Available area for container rows
    content_top = col_label_y + col_label_h + 2
    max_display = 8
    show_more = len(containers) > max_display
    display_containers = containers[:max_display]

    more_h = 28 if show_more else 0
    avail_h = h - content_top - pad_y - more_h
    row_count = len(display_containers)
    row_h = max(30, avail_h // max(row_count, 1))
    row_h = min(row_h, 48)

    bar_w = 200
    bar_h = 14

    for i, c in enumerate(display_containers):
        ry = content_top + i * row_h
        row_mid_y = ry + row_h // 2

        # Alternating row background
        if i % 2 == 0:
            draw.rectangle([0, ry, w, ry + row_h], fill=PANEL_BG)

        # Status dot
        dot_color = GREEN if c['running'] else RED
        dot_r = 5
        dot_cx = col_status_x + 6
        dot_cy = row_mid_y
        draw.ellipse([dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
                      fill=dot_color)

        # Container name (truncated)
        name = c['name'][:20]
        draw.text((col_name_x, row_mid_y - 10), name, fill=TEXT_BRIGHT, font=row_font)

        # CPU bar + percentage
        cpu_color = GREEN if c['cpu'] < 50 else YELLOW if c['cpu'] < 80 else RED
        bar_y = row_mid_y - bar_h // 2
        _draw_bar(draw, col_cpu_x, bar_y, bar_w, bar_h, c['cpu'], cpu_color)
        cpu_label = c['cpu_str']
        draw.text((col_cpu_x + bar_w + 8, row_mid_y - 8), cpu_label,
                  fill=TEXT, font=small_font)

        # MEM bar + usage
        mem_color = GREEN if c['mem_pct'] < 50 else YELLOW if c['mem_pct'] < 80 else RED
        _draw_bar(draw, col_mem_x, bar_y, bar_w, bar_h, c['mem_pct'], mem_color)
        mem_label = c['mem_usage']
        if len(mem_label) > 22:
            mem_label = mem_label[:22]
        draw.text((col_mem_x + bar_w + 8, row_mid_y - 8), mem_label,
                  fill=TEXT, font=small_font)

        # Net I/O
        net_text = c['net_io']
        if len(net_text) > 28:
            net_text = net_text[:28]
        draw.text((col_net_x, row_mid_y - 8), net_text, fill=CYAN, font=small_font)

        # PIDs
        draw.text((col_pids_x, row_mid_y - 8), c['pids'], fill=TEXT, font=small_font)

    # "and N more..." footer
    if show_more:
        extra = len(containers) - max_display
        more_text = f"... and {extra} more container{'s' if extra != 1 else ''}"
        more_y = content_top + row_count * row_h + 4
        draw.text((pad_x, more_y), more_text, fill=TEXT_DIM, font=label_font)

    return img
