"""Docker container status monitor for tinyscreen bar display."""

import subprocess
import time
from PIL import Image, ImageDraw, ImageFont

# --- Color palette (vivid, saturated — matching sysmon dashboard) ---
BG          = (5, 7, 12)
PANEL_BG    = (10, 14, 24)
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


# --- Utility ---
def _lerp_color(c1, c2, t):
    """Linearly interpolate between two RGB tuples."""
    t = max(0.0, min(1.0, t))
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


# --- Cached background and scanlines ---
_bg_cache = {}

def _get_bg(w, h):
    """Generate gradient background with grid, horizon glow, and vignette. Cached."""
    if (w, h) not in _bg_cache:
        img = Image.new('RGBA', (w, h), BG + (255,))
        draw = ImageDraw.Draw(img)
        # Vertical gradient
        for y in range(h):
            t = y / max(h - 1, 1)
            c = _lerp_color((8, 12, 22), (4, 6, 12), t)
            draw.line([(0, y), (w, y)], fill=c)
        # Grid
        grid_c = (18, 24, 38, 50)
        for gx in range(0, w, 40):
            draw.line([(gx, 0), (gx, h)], fill=grid_c)
        for gy in range(0, h, 40):
            draw.line([(0, gy), (w, gy)], fill=grid_c)
        # Grid dots at intersections
        for gx in range(0, w, 80):
            for gy in range(0, h, 80):
                draw.ellipse([gx - 2, gy - 2, gx + 2, gy + 2], fill=ACCENT + (25,))
                draw.ellipse([gx - 1, gy - 1, gx + 1, gy + 1], fill=ACCENT + (45,))
        # Bottom horizon glow
        for i in range(50):
            a = int(30 * (1.0 - i / 50))
            draw.line([(0, h - 1 - i), (w, h - 1 - i)], fill=(0, 100, 180, a))
        # Vignette
        vig = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        vd = ImageDraw.Draw(vig)
        for i in range(40):
            a = int(50 * (1.0 - i / 40))
            vd.line([(0, i), (w, i)], fill=(0, 0, 0, a))
            vd.line([(0, h - 1 - i), (w, h - 1 - i)], fill=(0, 0, 0, a))
        for i in range(60):
            a = int(40 * (1.0 - i / 60))
            vd.line([(i, 0), (i, h)], fill=(0, 0, 0, a))
            vd.line([(w - 1 - i, 0), (w - 1 - i, h)], fill=(0, 0, 0, a))
        img = Image.alpha_composite(img, vig)
        _bg_cache[(w, h)] = img
    return _bg_cache[(w, h)].copy()


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


# --- Glow bar ---
def _draw_bar(draw, img, x, y, w, h, pct, color):
    """Draw a horizontal progress bar with glow halo and gradient fill."""
    draw.rectangle([x, y, x + w, y + h], fill=(12, 15, 22))

    fill_w = max(0, int(w * min(pct, 100.0) / 100.0))
    if fill_w <= 0:
        return

    # Gradient fill: dim left -> bright right
    for col in range(fill_w):
        t = col / max(fill_w - 1, 1)
        c = _lerp_color(tuple(max(0, v - 80) for v in color), color, t * 0.8 + 0.2)
        draw.line([(x + 1 + col, y + 1), (x + 1 + col, y + h - 1)], fill=c)

    # Bright leading edge
    tip_w = min(4, fill_w)
    bright = tuple(min(255, c + 60) for c in color)
    draw.rectangle([x + fill_w - tip_w, y, x + fill_w, y + h], fill=bright)

    # Top highlight
    hl = tuple(min(255, c + 100) for c in color)
    draw.line([(x + 1, y + 1), (x + fill_w - 1, y + 1)], fill=hl)


# --- Status dot ---
def _draw_glow_dot(img, cx, cy, r, color):
    draw = ImageDraw.Draw(img)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    bright = tuple(min(255, c + 80) for c in color)
    core_r = max(1, r - 2)
    draw.ellipse([cx - core_r, cy - core_r, cx + core_r, cy + core_r], fill=bright)


def render_frame(w=1920, h=440):
    """Render one frame of the docker monitor. Returns a PIL Image."""
    _fetch_docker_data()

    # RGBA workflow — start with cached gradient background
    img = _get_bg(w, h)
    draw = ImageDraw.Draw(img)

    pad_x = 20
    pad_y = 6

    title_font = font(24)
    name_font = font(20)
    data_font = font(16)
    label_font = font(18)

    # --- Header with gradient fill and glowing accent line ---
    header_h = 44
    draw.rectangle([0, 0, w, header_h], fill=(11, 14, 27))

    # Accent line under header
    draw.rectangle([0, header_h - 2, w, header_h], fill=ACCENT)
    draw.rectangle([0, header_h, w, header_h + 2], fill=ACCENT + (50,))

    draw.text((pad_x, 11), "DOCKER CONTAINERS", fill=ACCENT, font=title_font)

    containers = _cache['stats']
    count_text = f"{len(containers)} running"
    if _cache['ps']:
        total = len(_cache['ps'])
        if total != len(containers):
            count_text = f"{len(containers)} active / {total} total"
    draw.text((w - pad_x - title_font.getlength(count_text), 12), count_text,
              fill=TEXT, font=title_font)

    # --- Handle empty / error states ---
    if _cache['error'] and not containers:
        msg = _cache['error']
        if 'not found' in msg.lower():
            msg = "Docker is not available"
        elif 'timed out' in msg.lower():
            msg = "Docker command timed out"
        msg_font = font(24)
        tw = msg_font.getlength(msg)
        draw.text(((w - tw) / 2, (h - 24) / 2), msg, fill=TEXT_DIM, font=msg_font)
        # Scanlines + convert
        img = Image.alpha_composite(img, _get_scanlines(w, h))
        out = Image.new('RGB', (w, h), BG)
        out.paste(img, (0, 0), img)
        return out

    if not containers:
        msg = "No containers running"
        msg_font = font(24)
        tw = msg_font.getlength(msg)
        draw.text(((w - tw) / 2, (h - 24) / 2), msg, fill=TEXT_DIM, font=msg_font)
        img = Image.alpha_composite(img, _get_scanlines(w, h))
        out = Image.new('RGB', (w, h), BG)
        out.paste(img, (0, 0), img)
        return out

    # --- Column labels row ---
    col_label_y = header_h + 4
    col_label_h = 22
    draw.rectangle([0, col_label_y, w, col_label_y + col_label_h], fill=(10, 13, 24))

    # Column positions — fit within 1920px without overlapping
    col_status_x = pad_x
    col_name_x = pad_x + 24
    col_cpu_x = 280
    col_mem_x = 580
    col_net_x = 880
    col_pids_x = 1200

    draw.text((col_name_x, col_label_y + 3), "CONTAINER", fill=TEXT_DIM, font=label_font)
    draw.text((col_cpu_x, col_label_y + 3), "CPU", fill=TEXT_DIM, font=label_font)
    draw.text((col_mem_x, col_label_y + 3), "MEMORY", fill=TEXT_DIM, font=label_font)
    draw.text((col_net_x, col_label_y + 3), "NET I/O", fill=TEXT_DIM, font=label_font)
    draw.text((col_pids_x, col_label_y + 3), "PIDs", fill=TEXT_DIM, font=label_font)

    # Separator line under column labels
    sep_y = col_label_y + col_label_h
    for i in range(3):
        a = int(120 * (1.0 - i / 3))
        draw.line([(0, sep_y + i), (w, sep_y + i)], fill=ACCENT + (a,))

    # --- Container rows ---
    content_top = sep_y + 4
    max_display = 8
    show_more = len(containers) > max_display
    display_containers = containers[:max_display]

    more_h = 28 if show_more else 0
    avail_h = h - content_top - pad_y - more_h - 12  # reserve space for bottom line
    row_count = len(display_containers)
    row_h = max(32, avail_h // max(row_count, 1))
    row_h = min(row_h, 48)

    bar_w = 160
    bar_h = 14

    for i, c in enumerate(display_containers):
        ry = content_top + i * row_h
        row_mid_y = ry + row_h // 2

        # Alternating row background
        if i % 2 == 0:
            draw.rectangle([0, ry, w, ry + row_h], fill=(11, 14, 25))
        else:
            draw.rectangle([0, ry, w, ry + row_h], fill=(7, 9, 16))

        # Status dot with glow
        dot_color = GREEN if c['running'] else RED
        dot_cx = col_status_x + 8
        dot_cy = row_mid_y
        _draw_glow_dot(img, dot_cx, dot_cy, 5, dot_color)
        # Re-acquire draw after paste operations
        draw = ImageDraw.Draw(img)

        # Container name
        name = c['name'][:24]
        draw.text((col_name_x, row_mid_y - 11), name, fill=TEXT_BRIGHT, font=name_font)

        # CPU bar + percentage
        cpu_color = GREEN if c['cpu'] < 50 else YELLOW if c['cpu'] < 80 else RED
        bar_y = row_mid_y - bar_h // 2
        _draw_bar(draw, img, col_cpu_x, bar_y, bar_w, bar_h, c['cpu'], cpu_color)
        draw = ImageDraw.Draw(img)  # re-acquire after paste
        cpu_label = c['cpu_str']
        draw.text((col_cpu_x + bar_w + 10, row_mid_y - 8), cpu_label,
                  fill=TEXT, font=data_font)

        # MEM bar + usage
        mem_color = GREEN if c['mem_pct'] < 50 else YELLOW if c['mem_pct'] < 80 else RED
        _draw_bar(draw, img, col_mem_x, bar_y, bar_w, bar_h, c['mem_pct'], mem_color)
        draw = ImageDraw.Draw(img)  # re-acquire after paste
        # Memory: just show used amount (not the full "X / limit" which is too long)
        mem_label = c['mem_usage'].split('/')[0].strip()
        draw.text((col_mem_x + bar_w + 10, row_mid_y - 8), mem_label,
                  fill=TEXT, font=data_font)

        # Net I/O — split into RX / TX on same line, truncated
        net_parts = c['net_io'].split('/')
        rx_text = net_parts[0].strip() if net_parts else ''
        tx_text = net_parts[1].strip() if len(net_parts) > 1 else ''
        draw.text((col_net_x, row_mid_y - 8), f"{rx_text}", fill=GREEN, font=data_font)
        draw.text((col_net_x + 130, row_mid_y - 8), f"/ {tx_text}", fill=ORANGE, font=data_font)

        # PIDs
        draw.text((col_pids_x, row_mid_y - 8), c['pids'], fill=PURPLE, font=data_font)

    # "and N more..." footer
    if show_more:
        extra = len(containers) - max_display
        more_text = f"... and {extra} more container{'s' if extra != 1 else ''}"
        more_y = content_top + row_count * row_h + 4
        draw.text((pad_x, more_y), more_text, fill=TEXT_DIM, font=data_font)

    # --- Bottom glowing accent line ---
    for i in range(8):
        a = int(180 * (1.0 - i / 8))
        draw.line([(0, h - 1 - i), (w, h - 1 - i)], fill=ACCENT + (a,))

    # Apply scanlines and convert RGBA -> RGB
    img = Image.alpha_composite(img, _get_scanlines(w, h))
    out = Image.new('RGB', (w, h), BG)
    out.paste(img, (0, 0), img)
    return out
