"""Network device scanner for tinyscreen bar display.

Discovers hosts on the local network via nmap ping scan.
Shows IPv4 address, hostname, and vendor/device type.
"""

import re
import subprocess
import time
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── Colors (vivid, matches other modes) ────────────────────────────
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

# ── Font cache ─────────────────────────────────────────────────────
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

# ── Visual utilities (same pattern as other modes) ─────────────────
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
                draw.ellipse([gx - 2, gy - 2, gx + 2, gy + 2], fill=ACCENT + (25,))
                draw.ellipse([gx - 1, gy - 1, gx + 1, gy + 1], fill=ACCENT + (45,))
        for i in range(50):
            a = int(30 * (1.0 - i / 50))
            draw.line([(0, h - 1 - i), (w, h - 1 - i)], fill=(0, 100, 180, a))
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
    key = (w, h)
    if key not in _scanline_cache:
        sl = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sl)
        for y in range(0, h, 3):
            sd.line([(0, y), (w, y)], fill=(0, 0, 0, 55))
        _scanline_cache[key] = sl
    return _scanline_cache[key]

def _draw_glow_dot(img, cx, cy, r, color):
    pad = 10
    size = (r + pad) * 2
    dot = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dot)
    dd.ellipse([pad - 3, pad - 3, pad + r * 2 + 3, pad + r * 2 + 3],
               fill=color + (60,))
    dot = dot.filter(ImageFilter.GaussianBlur(radius=5))
    img.paste(dot, (cx - r - pad, cy - r - pad), dot)
    sharp = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sharp)
    sd.ellipse([pad, pad, pad + r * 2, pad + r * 2], fill=color + (255,))
    bright = tuple(min(255, c + 80) for c in color)
    core = max(1, r - 2)
    sd.ellipse([pad + r - core, pad + r - core, pad + r + core, pad + r + core],
               fill=bright + (200,))
    img.paste(sharp, (cx - r - pad, cy - r - pad), sharp)

# ── Device type guessing ───────────────────────────────────────────
_DEVICE_TYPES = {
    'raspberry pi': ('Pi', PURPLE),
    'espressif': ('IoT', YELLOW),
    'sonos': ('Speaker', CYAN),
    'google': ('Google', ORANGE),
    'apple': ('Apple', TEXT_BRIGHT),
    'intel': ('PC', ACCENT),
    'actiontec': ('Router', GREEN),
    'samsung': ('Samsung', ACCENT),
    'amazon': ('Echo', ORANGE),
    'ring': ('Ring', CYAN),
    'wyze': ('Wyze', YELLOW),
    'tp-link': ('TP-Link', GREEN),
    'nest': ('Nest', ORANGE),
    'roku': ('Roku', PURPLE),
    'nvidia': ('GPU/PC', GREEN),
    'ai-link': ('IoT', YELLOW),
    'guangdong': ('Camera', RED),
    'china dragon': ('IoT', YELLOW),
    'unknown': ('Device', TEXT_DIM),
}

def _guess_device(hostname, vendor):
    """Guess device type from hostname and MAC vendor."""
    combined = f"{hostname} {vendor}".lower()
    for keyword, (label, color) in _DEVICE_TYPES.items():
        if keyword in combined:
            return label, color
    # Check hostname patterns
    if hostname and hostname != '':
        if 'cam' in hostname.lower() or 'ipc' in hostname.lower():
            return 'Camera', RED
        if 'phone' in hostname.lower() or 'iphone' in hostname.lower():
            return 'Phone', PURPLE
        if 'mac' in hostname.lower():
            return 'Mac', TEXT_BRIGHT
        if 'esp' in hostname.lower():
            return 'IoT', YELLOW
    return 'Device', TEXT_DIM

# ── Scan data ──────────────────────────────────────────────────────
_cache = {
    'hosts': [],
    'last_scan': 0,
    'scan_count': 0,
    'scanning': False,
    'error': None,
    'subnet': '192.168.1.0/24',
}

SCAN_INTERVAL = 30  # seconds between scans


def _detect_subnet():
    """Detect local subnet from default route."""
    try:
        out = subprocess.run(['ip', 'route'], capture_output=True, text=True, timeout=3)
        for line in out.stdout.splitlines():
            if line.startswith('default'):
                parts = line.split()
                src_idx = parts.index('src') if 'src' in parts else -1
                if src_idx > 0:
                    ip = parts[src_idx + 1]
                    # Convert to /24 subnet
                    octets = ip.split('.')
                    return f"{octets[0]}.{octets[1]}.{octets[2]}.0/24"
    except Exception:
        pass
    return '192.168.1.0/24'


def _scan_network():
    """Run nmap ping scan and parse results."""
    now = time.time()
    if now - _cache['last_scan'] < SCAN_INTERVAL:
        return
    if _cache['scanning']:
        return

    _cache['scanning'] = True
    _cache['last_scan'] = now

    subnet = _cache['subnet']
    try:
        result = subprocess.run(
            ['nmap', '-sn', subnet, '--host-timeout', '3s'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            _cache['error'] = 'nmap scan failed'
            _cache['scanning'] = False
            return

        hosts = []
        lines = result.stdout.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith('Nmap scan report for'):
                # Parse hostname and IP
                match = re.match(r'Nmap scan report for (?:(\S+) \()?(\d+\.\d+\.\d+\.\d+)\)?', line)
                if not match:
                    match = re.match(r'Nmap scan report for (\d+\.\d+\.\d+\.\d+)', line)
                if match:
                    groups = match.groups()
                    if len(groups) == 2 and groups[0]:
                        hostname = groups[0].replace('.lan', '')
                        ip = groups[1]
                    else:
                        hostname = ''
                        ip = groups[-1]

                    # Look for MAC address on next lines
                    vendor = ''
                    for j in range(i + 1, min(i + 4, len(lines))):
                        if 'MAC Address:' in lines[j]:
                            mac_match = re.search(r'MAC Address: \S+ \((.+)\)', lines[j])
                            if mac_match:
                                vendor = mac_match.group(1)
                            break

                    device_type, device_color = _guess_device(hostname, vendor)
                    hosts.append({
                        'ip': ip,
                        'hostname': hostname,
                        'vendor': vendor,
                        'type': device_type,
                        'color': device_color,
                    })
            i += 1

        # Sort by IP (numeric)
        hosts.sort(key=lambda h: tuple(int(o) for o in h['ip'].split('.')))
        _cache['hosts'] = hosts
        _cache['scan_count'] += 1
        _cache['error'] = None

    except subprocess.TimeoutExpired:
        _cache['error'] = 'Scan timed out'
    except FileNotFoundError:
        _cache['error'] = 'nmap not installed'
    except Exception as e:
        _cache['error'] = str(e)[:60]
    finally:
        _cache['scanning'] = False


def init():
    _cache['subnet'] = _detect_subnet()
    _cache['last_scan'] = 0
    _cache['scan_count'] = 0
    _scan_network()


# ── Render ─────────────────────────────────────────────────────────
def render_frame(w=1920, h=440):
    _scan_network()

    if not _cache['hosts'] and _cache['scan_count'] == 0:
        if not _cache.get('_init_done'):
            _cache['subnet'] = _detect_subnet()
            _cache['_init_done'] = True

    img = _get_bg(w, h)
    draw = ImageDraw.Draw(img)

    pad_x = 20
    hosts = _cache['hosts']

    # ── Header ──
    header_h = 44
    for row in range(header_h):
        t = row / max(header_h - 1, 1)
        c = _lerp_color((14, 18, 32), (8, 11, 22), t)
        draw.line([(0, row), (w, row)], fill=c)

    # Accent line
    glow = Image.new('RGBA', (w, 16), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rectangle([0, 0, w, 2], fill=ACCENT + (200,))
    gd.rectangle([0, 2, w, 6], fill=ACCENT + (50,))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=4))
    img.paste(glow, (0, header_h - 2), glow)

    draw = ImageDraw.Draw(img)
    draw.text((pad_x, 10), "NETWORK DEVICES", fill=ACCENT, font=font(24))

    status_text = f"{len(hosts)} hosts"
    if _cache['scanning']:
        status_text += "  scanning..."
    elif _cache['scan_count'] > 0:
        ago = int(time.time() - _cache['last_scan'])
        status_text += f"  scanned {ago}s ago"
    draw.text((w - pad_x - font(20).getlength(status_text), 14),
              status_text, fill=TEXT, font=font(20))

    # ── Error/empty state ──
    if _cache['error'] and not hosts:
        draw.text((w // 2 - 100, h // 2 - 12), _cache['error'],
                  fill=RED, font=font(24))
        img = Image.alpha_composite(img, _get_scanlines(w, h))
        out = Image.new('RGB', (w, h), BG)
        out.paste(img, (0, 0), img)
        return out

    if not hosts:
        draw.text((w // 2 - 80, h // 2 - 12), "Scanning network...",
                  fill=TEXT_DIM, font=font(24))
        img = Image.alpha_composite(img, _get_scanlines(w, h))
        out = Image.new('RGB', (w, h), BG)
        out.paste(img, (0, 0), img)
        return out

    # ── Column headers ──
    col_y = header_h + 6
    col_h = 24
    for row in range(col_h):
        t = row / max(col_h - 1, 1)
        c = _lerp_color((12, 16, 28), (8, 11, 20), t)
        draw.line([(0, col_y + row), (w, col_y + row)], fill=c)

    col_status_x = pad_x
    col_ip_x = pad_x + 30
    col_host_x = 280
    col_type_x = 620
    col_vendor_x = 780

    draw.text((col_ip_x, col_y + 3), "IP ADDRESS", fill=TEXT_DIM, font=font(18))
    draw.text((col_host_x, col_y + 3), "HOSTNAME", fill=TEXT_DIM, font=font(18))
    draw.text((col_type_x, col_y + 3), "TYPE", fill=TEXT_DIM, font=font(18))
    draw.text((col_vendor_x, col_y + 3), "VENDOR", fill=TEXT_DIM, font=font(18))

    # Separator
    sep_y = col_y + col_h
    for i in range(3):
        a = int(120 * (1.0 - i / 3))
        draw.line([(0, sep_y + i), (w, sep_y + i)], fill=ACCENT + (a,))

    # ── Two-column layout for more hosts ──
    content_top = sep_y + 4
    row_h = 26
    avail_h = h - content_top - 16
    rows_per_col = max(1, avail_h // row_h)

    # If more hosts than one column can show, use two columns
    use_two_cols = len(hosts) > rows_per_col
    if use_two_cols:
        col1_hosts = hosts[:rows_per_col]
        col2_hosts = hosts[rows_per_col:rows_per_col * 2]
        col2_offset = w // 2
    else:
        col1_hosts = hosts[:rows_per_col]
        col2_hosts = []
        col2_offset = 0

    def _draw_host_rows(host_list, x_offset, start_y):
        nonlocal draw
        for i, host in enumerate(host_list):
            ry = start_y + i * row_h
            if ry + row_h > h - 10:
                break

            # Alternating row
            if i % 2 == 0:
                for row in range(row_h):
                    t = row / max(row_h - 1, 1)
                    rc = _lerp_color((12, 16, 28), (10, 13, 22), t)
                    draw.line([(x_offset, ry + row),
                               (x_offset + (w // 2 if use_two_cols else w), ry + row)],
                              fill=rc)

            mid_y = ry + row_h // 2

            # Status dot
            _draw_glow_dot(img, x_offset + pad_x + 8, mid_y, 4, host['color'])
            draw = ImageDraw.Draw(img)

            # IP
            draw.text((x_offset + col_ip_x, mid_y - 9), host['ip'],
                      fill=TEXT_BRIGHT, font=font(18))

            # Hostname
            hostname = host['hostname'][:22] if host['hostname'] else '—'
            draw.text((x_offset + (col_host_x if not use_two_cols else 200),
                       mid_y - 9), hostname, fill=TEXT, font=font(18))

            # Type
            type_x = col_type_x if not use_two_cols else 440
            draw.text((x_offset + type_x, mid_y - 9), host['type'],
                      fill=host['color'], font=font(18))

            # Vendor (only in single-column mode — not enough space in 2-col)
            if not use_two_cols:
                vendor = host['vendor'][:40] if host['vendor'] else ''
                draw.text((x_offset + col_vendor_x, mid_y - 9), vendor,
                          fill=TEXT_DIM, font=font(16))

    _draw_host_rows(col1_hosts, 0, content_top)
    if col2_hosts:
        # Draw separator line between columns
        for y in range(content_top, h - 10):
            draw.point((col2_offset - 2, y), fill=ACCENT + (30,))
        _draw_host_rows(col2_hosts, col2_offset, content_top)

    # Show overflow count
    total_shown = len(col1_hosts) + len(col2_hosts)
    if len(hosts) > total_shown:
        extra = len(hosts) - total_shown
        draw.text((pad_x, h - 30), f"+ {extra} more devices",
                  fill=TEXT_DIM, font=font(16))

    # ── Bottom accent ──
    for i in range(8):
        a = int(180 * (1.0 - i / 8))
        draw.line([(0, h - 1 - i), (w, h - 1 - i)], fill=ACCENT + (a,))

    # Scanlines + convert
    img = Image.alpha_composite(img, _get_scanlines(w, h))
    out = Image.new('RGB', (w, h), BG)
    out.paste(img, (0, 0), img)
    return out


if __name__ == '__main__':
    import sys
    if '--once' in sys.argv:
        init()
        img = render_frame()
        img.save('/tmp/lanmap.png')
        print("Saved to /tmp/lanmap.png")
    else:
        print("Usage: python3 lanmap.py --once")
