"""
Matrix digital rain effect with real system data overlay
for tinyscreen bar display.
"""

import os
import random
import socket
import subprocess
import time
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# --- Visual style (dark theme) ---
BG = (10, 12, 18)
PANEL_BG = (18, 22, 32)
BORDER = (35, 42, 58)
ACCENT = (0, 170, 255)
TEXT = (200, 210, 225)
TEXT_DIM = (100, 110, 130)
GREEN = (0, 220, 100)
RED = (255, 60, 60)
CYAN = (0, 220, 220)
PURPLE = (160, 100, 255)

# Matrix-specific colours
MATRIX_HEAD = (220, 255, 220)
MATRIX_BRIGHT = (0, 255, 70)
MATRIX_MID = (0, 180, 50)
MATRIX_DIM = (0, 80, 25)
MATRIX_DARK = (0, 40, 12)
MATRIX_BG = (0, 0, 0)

# --- Font cache ---
_fonts = {}
def font(size):
    if size not in _fonts:
        for path in ['/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
                     '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
            try:
                _fonts[size] = ImageFont.truetype(path, size)
                return _fonts[size]
            except:
                continue
        _fonts[size] = ImageFont.load_default()
    return _fonts[size]

# --- Character pools ---
# Katakana range U+30A0..U+30FF, plus latin and digits
_katakana = [chr(c) for c in range(0x30A0, 0x3100)]
_latin = list('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')
_digits = list('0123456789')
_symbols = list('!@#$%^&*()-=+[]{}|;:<>?/~')
_char_pool = _katakana + _latin + _digits + _symbols


def _random_char():
    return random.choice(_char_pool)


class Column:
    """A single column of falling characters."""

    def __init__(self, x, rows, char_h):
        self.x = x
        self.rows = rows
        self.char_h = char_h
        self.speed = random.uniform(0.3, 1.5)  # rows per frame
        self.pos = random.uniform(-rows, 0)  # current head position (float)
        self.trail_len = random.randint(8, min(30, rows))
        self.chars = [_random_char() for _ in range(rows)]
        self._accumulator = 0.0

    def advance(self):
        self._accumulator += self.speed
        steps = int(self._accumulator)
        if steps > 0:
            self._accumulator -= steps
            self.pos += steps
            # Randomly mutate a character in the trail
            for _ in range(steps):
                idx = random.randint(0, self.rows - 1)
                self.chars[idx] = _random_char()
        # Reset when fully off screen
        if self.pos - self.trail_len > self.rows:
            self.pos = random.uniform(-self.trail_len, -2)
            self.speed = random.uniform(0.3, 1.5)
            self.trail_len = random.randint(8, min(30, self.rows))

    def draw(self, draw_ctx, f):
        head = int(self.pos)
        for i in range(self.trail_len):
            row = head - i
            if row < 0 or row >= self.rows:
                continue
            y = row * self.char_h
            ch = self.chars[row]
            if i == 0:
                color = MATRIX_HEAD
            elif i == 1:
                color = MATRIX_BRIGHT
            elif i < self.trail_len // 3:
                color = MATRIX_MID
            elif i < (2 * self.trail_len) // 3:
                color = MATRIX_DIM
            else:
                color = MATRIX_DARK
            draw_ctx.text((self.x, y), ch, fill=color, font=f)


# --- Module state ---
_columns = []
_initialized = False
_last_syslog = []
_last_syslog_time = 0
_hostname = ''
_ip_address = ''


def _get_syslog_lines(n=5):
    """Read last n lines from syslog or journalctl."""
    # Try /var/log/syslog first
    try:
        if os.path.isfile('/var/log/syslog'):
            with open('/var/log/syslog', 'r', errors='replace') as fh:
                lines = fh.readlines()
                return [l.strip() for l in lines[-n:]]
    except (PermissionError, OSError):
        pass
    # Fallback to journalctl
    try:
        result = subprocess.run(
            ['journalctl', '--no-pager', '-n', str(n), '--output=short'],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().splitlines()
            return lines[-n:]
    except Exception:
        pass
    return []


def _get_ip():
    """Get primary local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def _init_columns(w, h, char_w, char_h):
    """Create column objects spanning the full width."""
    global _columns, _initialized, _hostname, _ip_address
    num_cols = w // char_w
    rows = h // char_h + 1
    _columns = [Column(x=i * char_w, rows=rows, char_h=char_h) for i in range(num_cols)]
    _hostname = socket.gethostname()
    _ip_address = _get_ip()
    _initialized = True


def render_frame(w=1920, h=440):
    """Render one frame of the matrix rain effect."""
    global _last_syslog, _last_syslog_time

    f = font(16)
    # Measure character cell size
    bbox = f.getbbox('W')
    char_w = bbox[2] - bbox[0] + 2
    char_h = bbox[3] - bbox[1] + 4

    if not _initialized or len(_columns) == 0:
        _init_columns(w, h, char_w, char_h)

    img = Image.new('RGB', (w, h), MATRIX_BG)
    draw = ImageDraw.Draw(img)

    # Advance and draw columns
    for col in _columns:
        col.advance()
        col.draw(draw, f)

    # --- Overlay: syslog lines (refresh every 5 seconds) ---
    now = time.time()
    if now - _last_syslog_time > 5:
        _last_syslog = _get_syslog_lines(5)
        _last_syslog_time = now

    overlay_font = font(14)
    syslog_y = h - 20 * len(_last_syslog) - 10
    for i, line in enumerate(_last_syslog):
        # Truncate long lines
        disp = line[:160] if len(line) > 160 else line
        y_pos = syslog_y + i * 20
        if y_pos < 0:
            continue
        # Draw with a subtle dark background for readability
        text_bbox = draw.textbbox((10, y_pos), disp, font=overlay_font)
        draw.rectangle(
            [text_bbox[0] - 2, text_bbox[1] - 1, text_bbox[2] + 2, text_bbox[3] + 1],
            fill=(0, 0, 0, 180) if img.mode == 'RGBA' else (0, 5, 0),
        )
        draw.text((10, y_pos), disp, fill=MATRIX_BRIGHT, font=overlay_font)

    # --- Overlay: hostname, time, IP at fixed positions ---
    info_font = font(20)
    bold_font = font(24)

    # Hostname - top left
    draw.text((16, 10), _hostname, fill=MATRIX_HEAD, font=bold_font)

    # Time - top center
    time_str = datetime.now().strftime('%H:%M:%S')
    time_bbox = draw.textbbox((0, 0), time_str, font=bold_font)
    time_w = time_bbox[2] - time_bbox[0]
    draw.text(((w - time_w) // 2, 10), time_str, fill=MATRIX_HEAD, font=bold_font)

    # IP - top right
    ip_str = _ip_address
    ip_bbox = draw.textbbox((0, 0), ip_str, font=info_font)
    ip_w = ip_bbox[2] - ip_bbox[0]
    draw.text((w - ip_w - 16, 14), ip_str, fill=MATRIX_BRIGHT, font=info_font)

    return img
