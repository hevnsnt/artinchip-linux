"""Network connections monitor for tinyscreen bar display."""

import os
import struct
import socket
import subprocess
import time
from collections import Counter
from PIL import Image, ImageDraw, ImageFont, ImageFilter

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

# TCP state mapping (from kernel)
TCP_STATES = {
    '01': 'ESTABLISHED',
    '02': 'SYN_SENT',
    '03': 'SYN_RECV',
    '04': 'FIN_WAIT1',
    '05': 'FIN_WAIT2',
    '06': 'TIME_WAIT',
    '07': 'CLOSE',
    '08': 'CLOSE_WAIT',
    '09': 'LAST_ACK',
    '0A': 'LISTEN',
    '0B': 'CLOSING',
}

STATE_COLORS = {
    'ESTABLISHED': GREEN,
    'LISTEN': CYAN,
    'TIME_WAIT': TEXT_DIM,
    'CLOSE_WAIT': YELLOW,
    'SYN_SENT': ORANGE,
    'SYN_RECV': ORANGE,
    'FIN_WAIT1': PURPLE,
    'FIN_WAIT2': PURPLE,
    'CLOSE': RED,
    'LAST_ACK': RED,
    'CLOSING': RED,
}


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

# --- Data cache ---
_cache = {
    'connections': [],
    'last_proc_fetch': 0,
    'process_map': {},
    'last_ss_fetch': 0,
    'error': None,
}

PROC_REFRESH = 2    # seconds - read /proc
SS_REFRESH = 5      # seconds - run ss


def _parse_hex_addr_v4(hex_str):
    """Parse hex IP:port from /proc/net/tcp. Format: 'AABBCCDD:PORT'."""
    try:
        addr_hex, port_hex = hex_str.split(':')
        # IP is in little-endian dword
        ip_int = int(addr_hex, 16)
        ip_bytes = struct.pack('<I', ip_int)
        ip_str = socket.inet_ntoa(ip_bytes)
        port = int(port_hex, 16)
        return ip_str, port
    except Exception:
        return hex_str, 0


def _parse_hex_addr_v6(hex_str):
    """Parse hex IPv6:port from /proc/net/tcp6."""
    try:
        addr_hex, port_hex = hex_str.split(':')
        # IPv6 in /proc/net/tcp6 is four little-endian 32-bit words
        if len(addr_hex) != 32:
            return addr_hex, int(port_hex, 16)
        words = [addr_hex[i:i+8] for i in range(0, 32, 8)]
        ip_bytes = b''
        for w in words:
            ip_bytes += struct.pack('<I', int(w, 16))
        ip_str = socket.inet_ntop(socket.AF_INET6, ip_bytes)
        # Shorten common representations
        if ip_str.startswith('::ffff:'):
            ip_str = ip_str[7:]  # Show mapped IPv4 portion
        port = int(port_hex, 16)
        return ip_str, port
    except Exception:
        return hex_str, 0


def _read_proc_tcp(path, parser):
    """Read connections from a /proc/net/tcp* file."""
    connections = []
    try:
        with open(path, 'r') as f:
            lines = f.readlines()
        # Skip header line
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            local_addr_hex = parts[1]
            remote_addr_hex = parts[2]
            state_hex = parts[3]

            local_ip, local_port = parser(local_addr_hex)
            remote_ip, remote_port = parser(remote_addr_hex)

            state = TCP_STATES.get(state_hex, f'UNKNOWN({state_hex})')

            # Skip loopback
            if local_ip in ('127.0.0.1', '::1', '0.0.0.0') and \
               remote_ip in ('127.0.0.1', '::1', '0.0.0.0'):
                continue
            # For LISTEN sockets on 127.0.0.1, skip
            if local_ip == '127.0.0.1' and state == 'LISTEN':
                continue

            connections.append({
                'local_ip': local_ip,
                'local_port': local_port,
                'remote_ip': remote_ip,
                'remote_port': remote_port,
                'state': state,
                'process': '',
            })
    except Exception:
        pass
    return connections


def _fetch_ss_process_map():
    """Run ss -tunp to get process names for connections."""
    now = time.time()
    if now - _cache['last_ss_fetch'] < SS_REFRESH:
        return
    _cache['last_ss_fetch'] = now

    try:
        result = subprocess.run(
            ['ss', '-tunp'],
            capture_output=True, text=True, timeout=5
        )
        pmap = {}
        for line in result.stdout.strip().split('\n')[1:]:
            parts = line.split()
            if len(parts) < 6:
                continue
            local = parts[4]
            remote = parts[5]
            # Extract process name from the users: field
            proc_name = ''
            for p in parts[6:]:
                if 'users:' in p or '((' in p:
                    # Parse e.g. users:(("firefox",pid=1234,fd=45))
                    try:
                        start = p.index('((') + 2
                        end = p.index('"', start + 1) if '"' in p[start:] else p.index(',', start)
                        proc_name = p[start:end].strip('"')
                    except (ValueError, IndexError):
                        proc_name = p
                    break

            # Store by local:port -> remote:port as key
            key = f"{local}->{remote}"
            pmap[key] = proc_name

        _cache['process_map'] = pmap
    except Exception:
        pass


def _fetch_connections():
    """Fetch all connections from /proc/net/tcp and tcp6."""
    now = time.time()
    if now - _cache['last_proc_fetch'] < PROC_REFRESH:
        return
    _cache['last_proc_fetch'] = now

    _fetch_ss_process_map()

    connections = []
    connections += _read_proc_tcp('/proc/net/tcp', _parse_hex_addr_v4)
    connections += _read_proc_tcp('/proc/net/tcp6', _parse_hex_addr_v6)

    # Try to match process names from ss data
    pmap = _cache['process_map']
    for conn in connections:
        local_str = f"{conn['local_ip']}:{conn['local_port']}"
        remote_str = f"{conn['remote_ip']}:{conn['remote_port']}"
        key = f"{local_str}->{remote_str}"
        if key in pmap:
            conn['process'] = pmap[key]
        else:
            # Try wildcard local matches
            wild_local = f"*:{conn['local_port']}"
            wkey = f"{wild_local}->{remote_str}"
            if wkey in pmap:
                conn['process'] = pmap[wkey]
            else:
                # Try just port-based match from pmap
                for pk, pv in pmap.items():
                    if f":{conn['local_port']}" in pk.split('->')[0]:
                        conn['process'] = pv
                        break

    # Sort: ESTABLISHED first, then LISTEN, then others
    state_order = {
        'ESTABLISHED': 0, 'LISTEN': 1, 'SYN_SENT': 2, 'SYN_RECV': 3,
        'CLOSE_WAIT': 4, 'TIME_WAIT': 5, 'FIN_WAIT1': 6, 'FIN_WAIT2': 6,
    }
    connections.sort(key=lambda c: (state_order.get(c['state'], 9), c['local_port']))

    _cache['connections'] = connections
    _cache['error'] = None


def _draw_mini_bar_chart(img, draw, x, y, w, h, state_counts, total):
    """Draw a mini horizontal stacked bar chart with glow effect."""
    bar_bg = (12, 15, 22)
    if total == 0:
        draw.rounded_rectangle([x, y, x + w, y + h], radius=4, fill=bar_bg)
        return

    draw.rounded_rectangle([x, y, x + w, y + h], radius=4, fill=bar_bg)

    # Build sharp bar on a small RGBA layer
    bar_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bar_layer)

    ordered_states = ['ESTABLISHED', 'LISTEN', 'TIME_WAIT', 'CLOSE_WAIT', 'SYN_SENT']
    cx = 0
    for state in ordered_states:
        count = state_counts.get(state, 0)
        if count == 0:
            continue
        seg_w = max(1, int(w * count / total))
        color = STATE_COLORS.get(state, TEXT_DIM)
        if cx == 0:
            bd.rounded_rectangle([cx, 0, cx + seg_w, h], radius=4, fill=color + (255,))
        else:
            bd.rectangle([cx, 0, cx + seg_w, h], fill=color + (255,))
        cx += seg_w

    # Remaining / other states
    other_count = total - sum(state_counts.get(s, 0) for s in ordered_states)
    if other_count > 0 and cx < w:
        seg_w = max(1, int(w * other_count / total))
        bd.rectangle([cx, 0, cx + seg_w, h], fill=PURPLE + (255,))

    # Glow: blur a copy and paste underneath
    pad = 8
    glow_layer = Image.new('RGBA', (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    glow_layer.paste(bar_layer, (pad, pad), bar_layer)
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=6))
    img.paste(glow_layer, (x - pad, y - pad), glow_layer)

    # Sharp bar on top
    img.paste(bar_layer, (x, y), bar_layer)


def render_frame(w=1920, h=440):
    """Render one frame of the network monitor. Returns a PIL Image."""
    _fetch_connections()

    # RGBA workflow -- start with cached gradient background
    img = _get_bg(w, h)
    draw = ImageDraw.Draw(img)

    pad_x = 20
    title_font = font(24)
    summary_font = font(18)
    header_font = font(18)
    row_font = font(18)
    legend_font = font(16)
    more_font = font(16)

    connections = _cache['connections']

    # --- Header with gradient fill and glowing accent line ---
    header_h = 44
    top_c = (14, 18, 32)
    bot_c = (8, 11, 22)
    for row in range(header_h):
        t = row / max(header_h - 1, 1)
        c = _lerp_color(top_c, bot_c, t)
        draw.line([(0, row), (w, row)], fill=c)

    # Glowing accent line under header
    glow_w = w
    glow_h = 16
    glow = Image.new('RGBA', (glow_w, glow_h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rectangle([0, 0, glow_w, 2], fill=ACCENT + (200,))
    gd.rectangle([0, 2, glow_w, 6], fill=ACCENT + (50,))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=4))
    img.paste(glow, (0, header_h - 2), glow)
    draw = ImageDraw.Draw(img)

    draw.text((pad_x, 11), "NETWORK CONNECTIONS", fill=ACCENT, font=title_font)

    # --- Summary stats (colorful) ---
    state_counts = Counter(c['state'] for c in connections)
    total = len(connections)

    summary_y = header_h + 4
    summary_h = 24
    for row in range(summary_h):
        t = row / max(summary_h - 1, 1)
        c = _lerp_color((12, 16, 28), (8, 11, 20), t)
        draw.line([(0, summary_y + row), (w, summary_y + row)], fill=c)

    # Draw total count first
    total_text = f"{total} connections"
    draw.text((pad_x, summary_y + 3), total_text, fill=TEXT_BRIGHT, font=summary_font)
    sx = pad_x + summary_font.getlength(total_text) + 24

    # Then each state in its own color
    for state in ['ESTABLISHED', 'LISTEN', 'TIME_WAIT', 'CLOSE_WAIT', 'SYN_SENT']:
        cnt = state_counts.get(state, 0)
        if cnt == 0:
            continue
        cnt_str = f"{cnt} "
        draw.text((sx, summary_y + 3), cnt_str, fill=TEXT_BRIGHT, font=summary_font)
        sx += summary_font.getlength(cnt_str)
        state_color = STATE_COLORS.get(state, TEXT_DIM)
        draw.text((sx, summary_y + 3), state, fill=state_color, font=summary_font)
        sx += summary_font.getlength(state) + 20

    # --- Mini bar chart with glow ---
    bar_chart_y = summary_y + summary_h + 4
    bar_chart_h = 16
    _draw_mini_bar_chart(img, draw, pad_x, bar_chart_y, w - 2 * pad_x, bar_chart_h,
                         state_counts, total)
    draw = ImageDraw.Draw(img)  # re-acquire after paste operations

    # Legend for bar chart (colored dots with labels, spread wider)
    legend_y = bar_chart_y + bar_chart_h + 4
    legend_h = 20
    lx = pad_x
    for state_name, color in [('ESTABLISHED', GREEN), ('LISTEN', CYAN), ('TIME_WAIT', TEXT_DIM),
                               ('CLOSE_WAIT', YELLOW), ('SYN_SENT', ORANGE), ('OTHER', PURPLE)]:
        # Colored dot
        draw.ellipse([lx, legend_y + 4, lx + 10, legend_y + 14], fill=color)
        lx += 14
        draw.text((lx, legend_y + 1), state_name, fill=TEXT, font=legend_font)
        lx += legend_font.getlength(state_name) + 24

    # --- Column header ---
    col_header_y = legend_y + legend_h + 2
    col_header_h = 24
    for row in range(col_header_h):
        t = row / max(col_header_h - 1, 1)
        c = _lerp_color((12, 16, 28), (8, 11, 20), t)
        draw.line([(0, col_header_y + row), (w, col_header_y + row)], fill=c)

    # Separator line under column labels
    sep_y = col_header_y + col_header_h
    for i in range(3):
        a = int(120 * (1.0 - i / 3))
        draw.line([(0, sep_y + i), (w, sep_y + i)], fill=ACCENT + (a,))

    col_local_x = pad_x + 20
    col_remote_x = 480
    col_state_x = 960
    col_proc_x = 1200

    draw.text((col_local_x, col_header_y + 3), "LOCAL ADDRESS", fill=TEXT_DIM, font=header_font)
    draw.text((col_remote_x, col_header_y + 3), "REMOTE ADDRESS", fill=TEXT_DIM, font=header_font)
    draw.text((col_state_x, col_header_y + 3), "STATE", fill=TEXT_DIM, font=header_font)
    draw.text((col_proc_x, col_header_y + 3), "PROCESS", fill=TEXT_DIM, font=header_font)

    # --- Connection rows ---
    content_top = sep_y + 4
    row_h = 26
    max_rows = max(1, (h - content_top - 30) // row_h)

    if not connections:
        msg = "No active connections"
        msg_font = font(24)
        tw = msg_font.getlength(msg)
        draw.text(((w - tw) / 2, (h + content_top) / 2 - 12), msg,
                  fill=TEXT_DIM, font=msg_font)
        # Bottom glow + scanlines + convert
        for i in range(8):
            a = int(180 * (1.0 - i / 8))
            draw.line([(0, h - 1 - i), (w, h - 1 - i)], fill=ACCENT + (a,))
        img = Image.alpha_composite(img, _get_scanlines(w, h))
        out = Image.new('RGB', (w, h), BG)
        out.paste(img, (0, 0), img)
        return out

    display_conns = connections[:max_rows]

    for i, conn in enumerate(display_conns):
        ry = content_top + i * row_h

        # Alternating gradient rows
        if i % 2 == 0:
            for row in range(row_h):
                t = row / max(row_h - 1, 1)
                rc = _lerp_color((12, 16, 28, 40), (10, 13, 22, 30), t)
                draw.line([(0, ry + row), (w, ry + row)], fill=rc[:3])
        else:
            for row in range(row_h):
                t = row / max(row_h - 1, 1)
                rc = _lerp_color((8, 10, 18, 20), (6, 8, 14, 15), t)
                draw.line([(0, ry + row), (w, ry + row)], fill=rc[:3])

        state = conn['state']
        state_color = STATE_COLORS.get(state, TEXT_DIM)

        local_str = f"{conn['local_ip']}:{conn['local_port']}"
        remote_str = f"{conn['remote_ip']}:{conn['remote_port']}"
        if len(local_str) > 42:
            local_str = local_str[:42]
        if len(remote_str) > 42:
            remote_str = remote_str[:42]

        proc_name = conn.get('process', '')
        if len(proc_name) > 35:
            proc_name = proc_name[:35]

        text_y = ry + 4

        draw.text((col_local_x, text_y), local_str, fill=TEXT_BRIGHT, font=row_font)
        draw.text((col_remote_x, text_y), remote_str, fill=TEXT_BRIGHT, font=row_font)
        draw.text((col_state_x, text_y), state, fill=state_color, font=row_font)
        draw.text((col_proc_x, text_y), proc_name, fill=TEXT, font=row_font)

    # Show count if more connections than displayed
    if len(connections) > max_rows:
        extra = len(connections) - max_rows
        more_text = f"+ {extra} more connections not shown"
        more_y = h - 24
        draw.text((pad_x, more_y), more_text, fill=TEXT_DIM, font=more_font)

    # --- Bottom glowing accent line ---
    for i in range(8):
        a = int(180 * (1.0 - i / 8))
        draw.line([(0, h - 1 - i), (w, h - 1 - i)], fill=ACCENT + (a,))

    # Apply scanlines and convert RGBA -> RGB
    img = Image.alpha_composite(img, _get_scanlines(w, h))
    out = Image.new('RGB', (w, h), BG)
    out.paste(img, (0, 0), img)
    return out
