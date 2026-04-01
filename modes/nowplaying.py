"""
Now-playing media display mode for tinyscreen bar display.
Uses playerctl (or MPRIS D-Bus fallback) to show current track info
with album art placeholder, title, artist, and progress bar.
"""

import subprocess
import time
import math
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

# --- Cached media state ---
_media_cache = {}
_media_cache_time = 0
_CACHE_TTL = 1.0  # seconds
_playerctl_available = None


def _check_playerctl():
    """Check if playerctl is installed."""
    global _playerctl_available
    if _playerctl_available is not None:
        return _playerctl_available
    try:
        result = subprocess.run(
            ['playerctl', '--version'],
            capture_output=True, text=True, timeout=3,
        )
        _playerctl_available = result.returncode == 0
    except FileNotFoundError:
        _playerctl_available = False
    except Exception:
        _playerctl_available = False
    return _playerctl_available


def _get_media_playerctl():
    """Get media info via playerctl."""
    fmt = '{{title}}\t{{artist}}\t{{album}}\t{{mpris:artUrl}}\t{{status}}\t{{position}}\t{{mpris:length}}'
    try:
        result = subprocess.run(
            ['playerctl', 'metadata', '--format', fmt],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        parts = result.stdout.strip().split('\t')
        if len(parts) < 7:
            parts += [''] * (7 - len(parts))
        title, artist, album, art_url, status, position_us, length_us = parts[:7]

        # Convert microseconds to seconds
        try:
            position_s = int(position_us) / 1_000_000
        except (ValueError, TypeError):
            position_s = 0
        try:
            length_s = int(length_us) / 1_000_000
        except (ValueError, TypeError):
            length_s = 0

        return {
            'title': title or 'Unknown Title',
            'artist': artist or 'Unknown Artist',
            'album': album or '',
            'art_url': art_url or '',
            'status': status or 'Stopped',
            'position': position_s,
            'length': length_s,
        }
    except Exception:
        return None


def _get_media_dbus():
    """Fallback: try to get media info via dbus-send."""
    # First, find an MPRIS player on the bus
    try:
        result = subprocess.run(
            ['dbus-send', '--session', '--dest=org.freedesktop.DBus',
             '--type=method_call', '--print-reply',
             '/org/freedesktop/DBus', 'org.freedesktop.DBus.ListNames'],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return None
        # Find MPRIS player names
        player_name = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if 'org.mpris.MediaPlayer2.' in line:
                # Extract the bus name from the dbus output
                start = line.find('"')
                end = line.rfind('"')
                if start != -1 and end > start:
                    player_name = line[start + 1:end]
                    break
        if not player_name:
            return None

        # Get all properties
        result = subprocess.run(
            ['dbus-send', '--print-reply',
             '--dest={}'.format(player_name),
             '/org/mpris/MediaPlayer2',
             'org.freedesktop.DBus.Properties.GetAll',
             'string:org.mpris.MediaPlayer2.Player'],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return None

        output = result.stdout

        def _extract_string(text, key):
            """Rough extraction of a string value after a dict entry key."""
            idx = text.find('"{}"'.format(key))
            if idx == -1:
                return ''
            # Find the next string variant value
            rest = text[idx:]
            str_idx = rest.find('string "')
            if str_idx == -1:
                return ''
            start = str_idx + len('string "')
            end = rest.find('"', start)
            if end == -1:
                return ''
            return rest[start:end]

        def _extract_int64(text, key):
            """Rough extraction of an int64 value after a dict entry key."""
            idx = text.find('"{}"'.format(key))
            if idx == -1:
                return 0
            rest = text[idx:]
            for token in ['int64 ', 'int32 ', 'uint64 ']:
                pos = rest.find(token)
                if pos != -1:
                    val_start = pos + len(token)
                    val_str = ''
                    for ch in rest[val_start:]:
                        if ch.isdigit() or ch == '-':
                            val_str += ch
                        else:
                            break
                    try:
                        return int(val_str)
                    except ValueError:
                        return 0
            return 0

        title = _extract_string(output, 'xesam:title') or 'Unknown Title'
        artist = _extract_string(output, 'xesam:artist') or 'Unknown Artist'
        album = _extract_string(output, 'xesam:album') or ''
        art_url = _extract_string(output, 'mpris:artUrl') or ''
        status = _extract_string(output, 'PlaybackStatus') or 'Stopped'
        position = _extract_int64(output, 'Position')  # microseconds
        length = _extract_int64(output, 'mpris:length')  # microseconds

        return {
            'title': title,
            'artist': artist,
            'album': album,
            'art_url': art_url,
            'status': status,
            'position': position / 1_000_000,
            'length': length / 1_000_000,
        }
    except Exception:
        return None


def _get_media_info():
    """Get current media info with caching."""
    global _media_cache, _media_cache_time
    now = time.time()
    if now - _media_cache_time < _CACHE_TTL and _media_cache:
        return _media_cache

    info = None
    if _check_playerctl():
        info = _get_media_playerctl()
    if info is None:
        info = _get_media_dbus()

    _media_cache = info
    _media_cache_time = now
    return info


def _format_time(seconds):
    """Format seconds as M:SS."""
    if seconds <= 0:
        return '0:00'
    m = int(seconds) // 60
    s = int(seconds) % 60
    return '{}:{:02d}'.format(m, s)


def _draw_music_note(draw, cx, cy, size, color):
    """Draw a simple music note icon using lines and ellipses."""
    # Note head (filled ellipse)
    head_rx = int(size * 0.22)
    head_ry = int(size * 0.16)
    head_cx = cx - int(size * 0.05)
    head_cy = cy + int(size * 0.25)
    draw.ellipse(
        [head_cx - head_rx, head_cy - head_ry,
         head_cx + head_rx, head_cy + head_ry],
        fill=color,
    )
    # Stem (vertical line going up from right side of head)
    stem_x = head_cx + head_rx - 2
    stem_bottom = head_cy - head_ry + 4
    stem_top = cy - int(size * 0.35)
    draw.line([(stem_x, stem_bottom), (stem_x, stem_top)], fill=color, width=3)
    # Flag (curved line at top)
    flag_x_end = stem_x + int(size * 0.2)
    flag_y_end = stem_top + int(size * 0.2)
    draw.line([(stem_x, stem_top), (flag_x_end, flag_y_end)], fill=color, width=3)
    draw.line([(stem_x, stem_top + 8), (flag_x_end, flag_y_end + 6)], fill=color, width=2)


def _draw_nothing_playing(draw, w, h):
    """Draw a stylish 'Nothing Playing' screen."""
    # Large music note icon in center
    _draw_music_note(draw, w // 2, h // 2 - 30, 120, BORDER)

    # "Nothing Playing" text
    f = font(32)
    msg = "Nothing Playing"
    bbox = draw.textbbox((0, 0), msg, font=f)
    tw = bbox[2] - bbox[0]
    draw.text(((w - tw) // 2, h // 2 + 50), msg, fill=TEXT_DIM, font=f)

    # Subtle horizontal line
    line_y = h // 2 + 95
    line_margin = w // 4
    draw.line([(line_margin, line_y), (w - line_margin, line_y)], fill=BORDER, width=1)

    # Hint text
    if not _check_playerctl():
        hint = "Install playerctl for media info"
        hf = font(16)
        hbbox = draw.textbbox((0, 0), hint, font=hf)
        htw = hbbox[2] - hbbox[0]
        draw.text(((w - htw) // 2, line_y + 12), hint, fill=TEXT_DIM, font=hf)


def _generate_art_color(title, artist):
    """Generate a deterministic colour from title+artist for the art placeholder."""
    seed = hash(title + artist) & 0xFFFFFF
    r = 40 + ((seed >> 16) & 0xFF) % 80
    g = 40 + ((seed >> 8) & 0xFF) % 80
    b = 40 + (seed & 0xFF) % 80
    return (r, g, b)


def render_frame(w=1920, h=440):
    """Render one frame of the now-playing display."""
    img = Image.new('RGB', (w, h), BG)
    draw = ImageDraw.Draw(img)

    info = _get_media_info()

    if info is None:
        _draw_nothing_playing(draw, w, h)
        return img

    title = info['title']
    artist = info['artist']
    album = info['album']
    status = info['status']
    position = info['position']
    length = info['length']

    # --- Layout constants ---
    padding = 30
    art_size = h - padding * 2 - 60  # leave room for progress bar at bottom
    art_x = padding
    art_y = padding

    # --- Album art placeholder ---
    art_color = _generate_art_color(title, artist)
    draw.rectangle(
        [art_x, art_y, art_x + art_size, art_y + art_size],
        fill=art_color,
        outline=BORDER,
        width=2,
    )
    # Draw a music note inside the art placeholder
    _draw_music_note(
        draw,
        art_x + art_size // 2,
        art_y + art_size // 2,
        art_size // 2,
        (art_color[0] + 40, art_color[1] + 40, art_color[2] + 40),
    )

    # --- Text area ---
    text_x = art_x + art_size + padding
    text_max_w = w - text_x - padding

    # Status indicator (Playing / Paused)
    status_font = font(16)
    if status.lower() == 'playing':
        status_color = GREEN
        status_icon = ">>> PLAYING"
    elif status.lower() == 'paused':
        status_color = ACCENT
        status_icon = "|| PAUSED"
    else:
        status_color = TEXT_DIM
        status_icon = "[] STOPPED"
    draw.text((text_x, art_y), status_icon, fill=status_color, font=status_font)

    # Title (large)
    title_font = font(42)
    title_y = art_y + 28
    # Truncate title if too long
    display_title = title
    while True:
        bbox = draw.textbbox((0, 0), display_title, font=title_font)
        if bbox[2] - bbox[0] <= text_max_w or len(display_title) <= 3:
            break
        display_title = display_title[:-4] + '...'
    draw.text((text_x, title_y), display_title, fill=TEXT, font=title_font)

    # Artist
    artist_font = font(28)
    artist_y = title_y + 55
    draw.text((text_x, artist_y), artist, fill=ACCENT, font=artist_font)

    # Album (if available)
    if album:
        album_font = font(20)
        album_y = artist_y + 40
        draw.text((text_x, album_y), album, fill=TEXT_DIM, font=album_font)

    # --- Progress bar (full width near bottom) ---
    bar_h = 8
    bar_y = h - padding - bar_h - 18
    bar_x_start = padding
    bar_x_end = w - padding
    bar_width = bar_x_end - bar_x_start

    # Background track
    draw.rectangle(
        [bar_x_start, bar_y, bar_x_end, bar_y + bar_h],
        fill=PANEL_BG,
        outline=BORDER,
        width=1,
    )

    # Progress fill
    if length > 0:
        progress = min(max(position / length, 0.0), 1.0)
    else:
        progress = 0.0

    fill_w = int(bar_width * progress)
    if fill_w > 0:
        draw.rectangle(
            [bar_x_start, bar_y, bar_x_start + fill_w, bar_y + bar_h],
            fill=ACCENT,
        )
        # Bright dot at the progress head
        dot_r = 6
        dot_x = bar_x_start + fill_w
        dot_y = bar_y + bar_h // 2
        draw.ellipse(
            [dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r],
            fill=TEXT,
        )

    # Time labels
    time_font = font(16)
    elapsed_str = _format_time(position)
    total_str = _format_time(length)
    draw.text((bar_x_start, bar_y + bar_h + 4), elapsed_str, fill=TEXT_DIM, font=time_font)
    total_bbox = draw.textbbox((0, 0), total_str, font=time_font)
    total_tw = total_bbox[2] - total_bbox[0]
    draw.text((bar_x_end - total_tw, bar_y + bar_h + 4), total_str, fill=TEXT_DIM, font=time_font)

    return img
