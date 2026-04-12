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
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

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

# Matrix-specific colours — dramatic gradient from white-hot head to deep green
MATRIX_HEAD = (255, 255, 255)
MATRIX_BRIGHT = (150, 255, 150)
MATRIX_MID = (0, 255, 65)
MATRIX_DIM = (0, 180, 40)
MATRIX_DARK = (0, 100, 25)
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

# --- Scanline overlay cache ---
_scanline_cache = {}

def _get_scanlines(w, h):
    """Semi-transparent horizontal scanlines for CRT effect. Cached."""
    key = (w, h)
    if key not in _scanline_cache:
        sl = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sl)
        for y in range(0, h, 3):
            sd.line([(0, y), (w, y)], fill=(0, 0, 0, 55))
        _scanline_cache[key] = sl
    return _scanline_cache[key]

# --- Character pools ---
# Katakana range U+30A0..U+30FF, plus latin and digits
_katakana = [chr(c) for c in range(0x30A0, 0x3100)]
_latin = list('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')
_digits = list('0123456789')
_symbols = list('!@#$%^&*()-=+[]{}|;:<>?/~')
_char_pool = _katakana + _latin + _digits + _symbols


def _random_char():
    return random.choice(_char_pool)


def _cheap_bloom(img, strength=0.35):
    """Fast bloom: downscale, blur at small radius, upscale, blend."""
    w, h = img.size
    small = img.resize((w // 4, h // 4), Image.BOX)
    small = small.filter(ImageFilter.GaussianBlur(radius=2))
    bloom = small.resize((w, h), Image.BILINEAR)
    bloom = ImageEnhance.Brightness(bloom).enhance(strength)
    return ImageChops.add(img, bloom)


class Column:
    """A single column of falling characters."""

    def __init__(self, x, rows, char_h):
        self.x = x
        self.rows = rows
        self.char_h = char_h
        self.depth = random.uniform(0.3, 1.0)
        self.speed = random.uniform(0.3, 1.5) * self.depth
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
            self.depth = random.uniform(0.3, 1.0)
            self.speed = random.uniform(0.3, 1.5) * self.depth
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
            # Scale color by depth — far columns are dimmer
            color = tuple(int(c * self.depth) for c in color)
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

    # --- Dim rain to background level ---
    img = ImageEnhance.Brightness(img).enhance(0.55)
    img = _cheap_bloom(img)

    img_rgba = img.convert('RGBA')
    draw = ImageDraw.Draw(img_rgba)

    # --- Syslog: THE PRIMARY CONTENT ---
    # Fetch more lines, refresh more often
    now = time.time()
    if now - _last_syslog_time > 3:
        _last_syslog = _get_syslog_lines(15)
        _last_syslog_time = now

    header_h = 42
    draw = ImageDraw.Draw(img_rgba)

    # Hostname / time / IP — with drop shadow for readability over rain
    bold_font = font(26)
    def _shadow_text(draw, x, y, text, color, f):
        draw.text((x + 1, y + 1), text, fill=(0, 0, 0, 255), font=f)
        draw.text((x, y), text, fill=color, font=f)

    _shadow_text(draw, 16, 8, _hostname, MATRIX_HEAD, bold_font)
    time_str = datetime.now().strftime('%H:%M:%S')
    time_bbox = draw.textbbox((0, 0), time_str, font=bold_font)
    time_w = time_bbox[2] - time_bbox[0]
    _shadow_text(draw, (w - time_w) // 2, 8, time_str, MATRIX_HEAD, bold_font)
    ip_font = font(20)
    ip_bbox = draw.textbbox((0, 0), _ip_address, font=ip_font)
    ip_w = ip_bbox[2] - ip_bbox[0]
    _shadow_text(draw, w - ip_w - 16, 12, _ip_address, MATRIX_BRIGHT, ip_font)

    # Log lines — large, filling most of the screen
    log_font = font(20)
    line_h = 28
    log_top = header_h + 4
    max_lines = (h - log_top - 8) // line_h
    lines_to_show = _last_syslog[-max_lines:] if _last_syslog else []

    for i, line in enumerate(lines_to_show):
        y_pos = log_top + i * line_h
        # Truncate to fit width
        disp = line[:140] if len(line) > 140 else line

        # No background bar — text sits directly on the rain

        # Cyberpunk color coding
        lower = disp.lower()
        if any(kw in lower for kw in ['error', 'fail', 'crit', 'fatal']):
            text_color = (255, 40, 80, 255)   # hot pink / neon red
        elif any(kw in lower for kw in ['warn', 'timeout', 'denied']):
            text_color = (255, 180, 0, 255)   # amber
        elif any(kw in lower for kw in ['start', 'up', 'connect', 'success', 'ok']):
            text_color = (0, 255, 180, 255)   # neon teal
        elif any(kw in lower for kw in ['session', 'auth', 'login', 'ssh']):
            text_color = (200, 80, 255, 255)  # neon purple
        else:
            text_color = (0, 230, 255, 255)   # cyan

        _shadow_text(draw, 16, y_pos, disp, text_color, log_font)

    # --- Scanline overlay ---
    img_rgba = Image.alpha_composite(img_rgba, _get_scanlines(w, h))
    img = img_rgba.convert('RGB')

    return img
