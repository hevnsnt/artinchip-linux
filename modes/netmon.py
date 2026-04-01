"""Network connections monitor for tinyscreen bar display."""

import os
import struct
import socket
import subprocess
import time
from collections import Counter
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


def _draw_mini_bar_chart(draw, x, y, w, h, state_counts, total):
    """Draw a mini horizontal stacked bar chart of connection states."""
    if total == 0:
        draw.rounded_rectangle([x, y, x + w, y + h], radius=3, fill=BORDER)
        return

    draw.rounded_rectangle([x, y, x + w, y + h], radius=3, fill=BORDER)

    # Draw stacked bar proportionally
    ordered_states = ['ESTABLISHED', 'LISTEN', 'TIME_WAIT', 'CLOSE_WAIT', 'SYN_SENT']
    cx = x
    for state in ordered_states:
        count = state_counts.get(state, 0)
        if count == 0:
            continue
        seg_w = max(1, int(w * count / total))
        color = STATE_COLORS.get(state, TEXT_DIM)
        if cx == x:
            draw.rounded_rectangle([cx, y, cx + seg_w, y + h], radius=3, fill=color)
        else:
            draw.rectangle([cx, y, cx + seg_w, y + h], fill=color)
        cx += seg_w

    # Draw remaining states
    other_count = total - sum(state_counts.get(s, 0) for s in ordered_states)
    if other_count > 0 and cx < x + w:
        seg_w = max(1, int(w * other_count / total))
        draw.rectangle([cx, y, cx + seg_w, y + h], fill=PURPLE)


def render_frame(w, h):
    """Render one frame of the network monitor. Returns a PIL Image."""
    img = Image.new('RGB', (w, h), BG)
    draw = ImageDraw.Draw(img)

    _fetch_connections()

    pad_x = 12
    title_font = font(22)
    label_font = font(16)
    row_font = font(15)
    small_font = font(13)

    connections = _cache['connections']

    # --- Header area ---
    header_h = 38
    draw.rectangle([0, 0, w, header_h], fill=PANEL_BG)
    draw.line([0, header_h, w, header_h], fill=BORDER, width=1)

    draw.text((pad_x, 8), "NETWORK CONNECTIONS", fill=ACCENT, font=title_font)

    # --- Summary stats ---
    state_counts = Counter(c['state'] for c in connections)
    total = len(connections)

    summary_parts = [f"{total} connections:"]
    for state in ['ESTABLISHED', 'LISTEN', 'TIME_WAIT', 'CLOSE_WAIT', 'SYN_SENT']:
        cnt = state_counts.get(state, 0)
        if cnt > 0:
            summary_parts.append(f"{cnt} {state}")

    summary_text = "  ".join(summary_parts)
    summary_y = header_h + 4
    summary_h = 22
    draw.rectangle([0, summary_y, w, summary_y + summary_h], fill=PANEL_BG)
    draw.text((pad_x, summary_y + 3), summary_text, fill=TEXT, font=label_font)

    # --- Mini bar chart ---
    bar_chart_y = summary_y + summary_h + 2
    bar_chart_h = 10
    _draw_mini_bar_chart(draw, pad_x, bar_chart_y, w - 2 * pad_x, bar_chart_h,
                         state_counts, total)

    # Legend for bar chart (small, inline)
    legend_y = bar_chart_y + bar_chart_h + 2
    legend_h = 16
    lx = pad_x
    for state_name, color in [('EST', GREEN), ('LISTEN', CYAN), ('TW', TEXT_DIM),
                               ('CW', YELLOW), ('SYN', ORANGE)]:
        draw.rectangle([lx, legend_y + 2, lx + 8, legend_y + 10], fill=color)
        lx += 12
        draw.text((lx, legend_y), state_name, fill=TEXT_DIM, font=small_font)
        lx += small_font.getlength(state_name) + 12

    # --- Column header ---
    col_header_y = legend_y + legend_h + 2
    col_header_h = 20
    draw.rectangle([0, col_header_y, w, col_header_y + col_header_h], fill=PANEL_BG)
    draw.line([0, col_header_y + col_header_h, w, col_header_y + col_header_h],
              fill=BORDER, width=1)

    col_local_x = pad_x
    col_remote_x = 420
    col_state_x = 860
    col_proc_x = 1060

    draw.text((col_local_x, col_header_y + 2), "LOCAL ADDRESS", fill=TEXT_DIM, font=small_font)
    draw.text((col_remote_x, col_header_y + 2), "REMOTE ADDRESS", fill=TEXT_DIM, font=small_font)
    draw.text((col_state_x, col_header_y + 2), "STATE", fill=TEXT_DIM, font=small_font)
    draw.text((col_proc_x, col_header_y + 2), "PROCESS", fill=TEXT_DIM, font=small_font)

    # --- Connection rows ---
    content_top = col_header_y + col_header_h + 2
    row_h = 22
    max_rows = max(1, (h - content_top - 4) // row_h)

    if not connections:
        msg = "No active connections"
        msg_font = font(24)
        tw = msg_font.getlength(msg)
        draw.text(((w - tw) / 2, (h + content_top) / 2 - 12), msg,
                  fill=TEXT_DIM, font=msg_font)
        return img

    display_conns = connections[:max_rows]

    for i, conn in enumerate(display_conns):
        ry = content_top + i * row_h

        if i % 2 == 0:
            draw.rectangle([0, ry, w, ry + row_h], fill=PANEL_BG)

        state = conn['state']
        state_color = STATE_COLORS.get(state, TEXT_DIM)

        local_str = f"{conn['local_ip']}:{conn['local_port']}"
        remote_str = f"{conn['remote_ip']}:{conn['remote_port']}"
        if len(local_str) > 40:
            local_str = local_str[:40]
        if len(remote_str) > 40:
            remote_str = remote_str[:40]

        proc_name = conn.get('process', '')
        if len(proc_name) > 30:
            proc_name = proc_name[:30]

        text_y = ry + 3

        draw.text((col_local_x, text_y), local_str, fill=TEXT, font=row_font)
        draw.text((col_remote_x, text_y), remote_str, fill=TEXT, font=row_font)
        draw.text((col_state_x, text_y), state, fill=state_color, font=row_font)
        draw.text((col_proc_x, text_y), proc_name, fill=TEXT_DIM, font=row_font)

    # Show count if more connections than displayed
    if len(connections) > max_rows:
        extra = len(connections) - max_rows
        more_text = f"+ {extra} more connections not shown"
        more_y = h - 20
        draw.text((pad_x, more_y), more_text, fill=TEXT_DIM, font=small_font)

    return img
