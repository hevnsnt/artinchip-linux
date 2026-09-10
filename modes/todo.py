"""
Todo list mode for tinyscreen bar display.

Renders today's tasks plus the next 3 days from a JSON file. The file is
re-read on every frame, so edits appear within a second, no restart needed.

Data file: ~/.tinyscreen/todo.json  (override with TINYSCREEN_TODO env var)

Format:
{
  "tasks": [
    {"title": "Pay estimated taxes", "due": "2026-09-10", "time": "09:00",
     "done": false, "tags": ["finance"]},
    {"title": "Call Mike", "due": "2026-09-11", "done": false}
  ]
}

Fields: title (required), due (YYYY-MM-DD, defaults to today), time (HH:MM,
optional), done (bool, optional), tags (list of strings, optional).
"""

import json
import os
import sys as _sys
import time
from datetime import date, datetime, timedelta

from PIL import Image, ImageDraw, ImageFont

IS_MAC = _sys.platform == 'darwin'

TODOFILE = os.environ.get('TINYSCREEN_TODO', os.path.expanduser('~/.tinyscreen/todo.json'))

# --- Visual style (matches sysmon) ---
BG = (5, 7, 12)
PANEL_BG = (10, 14, 24)
ACCENT = (0, 210, 255)
ACCENT_DIM = (0, 80, 150)
TEXT = (220, 225, 240)
TEXT_DIM = (65, 75, 100)
TEXT_BRIGHT = (252, 254, 255)
GREEN = (0, 255, 140)
YELLOW = (255, 225, 0)
RED = (255, 70, 70)
ORANGE = (255, 165, 30)
PURPLE = (160, 110, 255)
CYAN = (0, 240, 255)

# --- Font cache ---
_fonts = {}
def font(size):
    if size not in _fonts:
        for path in ['/System/Library/Fonts/Menlo.ttc',
                     '/System/Library/Fonts/Helvetica.ttc',
                     '/System/Library/Fonts/HelveticaNeue.ttc',
                     '/System/Library/Fonts/SFNS.ttf',
                     '/Library/Fonts/Arial Unicode.ttf',
                     '/Library/Fonts/Arial.ttf',
                     '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
                     '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
            try:
                _fonts[size] = ImageFont.truetype(path, size)
                return _fonts[size]
            except Exception:
                continue
        _fonts[size] = ImageFont.load_default()
    return _fonts[size]


# --- Data loading ---
_cache = {'mtime': None, 'tasks': []}

def _parse_date(s):
    if not s:
        return date.today()
    try:
        return date.fromisoformat(str(s).strip())
    except Exception:
        return date.today()

def _load_tasks():
    """Read todo.json, caching on file mtime."""
    try:
        mtime = os.path.getmtime(TODOFILE)
        if mtime == _cache['mtime']:
            return _cache['tasks']
        with open(TODOFILE) as f:
            data = json.load(f)
        tasks = data.get('tasks', []) if isinstance(data, dict) else []
        _cache['mtime'] = mtime
        _cache['tasks'] = tasks
        return tasks
    except FileNotFoundError:
        _cache['mtime'] = None
        _cache['tasks'] = []
        return None  # distinguish "no file" from "empty file"
    except Exception:
        return _cache['tasks']


def _task_sort_key(t):
    tme = str(t.get('time', '') or '')
    return (tme, str(t.get('title', '')))

def _split_tasks(tasks, today):
    """Return (overdue, today_pending, today_done, upcoming[3])."""
    overdue, today_pending, today_done = [], [], []
    upcoming = {today + timedelta(days=i): [] for i in range(1, 4)}
    for t in tasks:
        d = _parse_date(t.get('due'))
        done = bool(t.get('done'))
        if d < today and not done:
            overdue.append(t)
        elif d == today:
            (today_done if done else today_pending).append(t)
        elif d in upcoming and not done:
            upcoming[d].append(t)
    overdue.sort(key=_task_sort_key)
    today_pending.sort(key=_task_sort_key)
    today_done.sort(key=_task_sort_key)
    for d in upcoming:
        upcoming[d].sort(key=_task_sort_key)
    return overdue, today_pending, today_done, upcoming


# --- Drawing helpers ---
def _lerp_color(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

def _draw_panel_bg(draw, x, y, w, h):
    for row in range(h):
        t = row / max(h - 1, 1)
        c = _lerp_color((14, 18, 30), (8, 11, 20), t)
        draw.line([(x, y + row), (x + w, y + row)], fill=c)

def _draw_header(img, draw, w):
    for row in range(48):
        t = row / 47
        c = _lerp_color((14, 18, 32), (8, 11, 22), t)
        draw.line([(0, row), (w, row)], fill=c)
    # glowing accent line under header
    glow = Image.new('RGBA', (w, 16), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rectangle([0, 0, w, 2], fill=ACCENT + (200,))
    gd.rectangle([0, 2, w, 6], fill=ACCENT + (50,))
    from PIL import ImageFilter
    glow = glow.filter(ImageFilter.GaussianBlur(radius=4))
    img.paste(glow, (0, 46), glow)
    draw = ImageDraw.Draw(img)
    return draw

def _truncate(draw, text, f, max_w):
    if draw.textlength(text, font=f) <= max_w:
        return text
    while text and draw.textlength(text + '...', font=f) > max_w:
        text = text[:-1]
    return text + '...'

def _draw_task_row(draw, x, y, task, max_w, time_w, done=False, overdue=False):
    """Draw one task row. Returns nothing."""
    color = TEXT_DIM if done else TEXT_BRIGHT
    if overdue:
        color = RED
    # checkbox
    box = [x, y + 4, x + 16, y + 20]
    if done:
        draw.rectangle(box, fill=GREEN)
        draw.line([(x + 3, y + 12), (x + 7, y + 16), (x + 13, y + 7)], fill=(0, 0, 0), width=2)
    else:
        draw.rectangle(box, outline=ACCENT_DIM, width=1)
    # time
    tme = str(task.get('time', '') or '')
    tx = x + 24
    if tme:
        draw.text((tx, y), tme, fill=ACCENT if not done else TEXT_DIM, font=font(16))
        tx += time_w
    # title
    title = _truncate(draw, str(task.get('title', '')), font(17), max_w - (tx - x))
    draw.text((tx, y), title, fill=color, font=font(17))
    # tags
    tags = task.get('tags') or []
    if tags and not done:
        tag_str = ' '.join('#' + str(t) for t in tags)
        tag_str = _truncate(draw, tag_str, font(13), 140)
        draw.text((x + max_w - 140, y + 2), tag_str, fill=PURPLE, font=font(13))


def render_frame(w=1920, h=440):
    """Render the todo dashboard. Returns a PIL Image (RGB)."""
    img = Image.new('RGB', (w, h), BG)
    draw = ImageDraw.Draw(img)

    # Background gradient
    for y in range(h):
        t = y / max(h - 1, 1)
        c = _lerp_color((8, 12, 22), (4, 6, 12), t)
        draw.line([(0, y), (w, y)], fill=c)

    draw = _draw_header(img, draw, w)

    today = date.today()
    today_str = today.strftime('%A, %b %d')
    draw.text((20, 12), 'TASKS', fill=ACCENT, font=font(24))
    draw.text((w - 20 - draw.textlength(today_str, font=font(20)), 14),
              today_str, fill=TEXT_DIM, font=font(20))

    tasks = _load_tasks()

    if tasks is None:
        msg = 'No todo list yet. Create ~/.tinyscreen/todo.json'
        draw.text((40, h // 2 - 20), msg, fill=TEXT_DIM, font=font(22))
        hint = 'Ask your agent to add tasks, or run: todo-cli.py add "Task title"'
        draw.text((40, h // 2 + 10), hint, fill=TEXT_DIM, font=font(16))
        return img

    overdue, today_pending, today_done, upcoming = _split_tasks(tasks, today)

    # --- Layout ---
    pad = 16
    body_y = 56
    body_h = h - body_y - 8
    left_w = 1010
    right_x = left_w + 12
    right_w = w - right_x - pad
    col_gap = 8
    col_w = (right_w - 2 * col_gap) // 3

    # --- Left: TODAY ---
    _draw_panel_bg(draw, pad, body_y, left_w - pad, body_h)
    draw.text((pad + 12, body_y + 8), 'TODAY', fill=ACCENT, font=font(20))
    pending_count = len(today_pending)
    draw.text((pad + 12 + draw.textlength('TODAY', font=font(20)) + 14, body_y + 11),
              f'{pending_count} open', fill=TEXT_DIM, font=font(15))

    row_h = 30
    y = body_y + 40
    max_rows = (body_h - 44) // row_h

    # Overdue first
    for t in overdue:
        if y + row_h > body_y + body_h - 6:
            break
        _draw_task_row(draw, pad + 12, y, t, left_w - pad - 24, 58, overdue=True)
        y += row_h
    # Pending
    for t in today_pending:
        if y + row_h > body_y + body_h - 6:
            break
        _draw_task_row(draw, pad + 12, y, t, left_w - pad - 24, 58)
        y += row_h
    # Done (dimmed)
    if today_done:
        y += 2
        draw.text((pad + 12, y - 2), 'DONE', fill=TEXT_DIM, font=font(14))
        y += 18
        for t in today_done:
            if y + row_h > body_y + body_h - 6:
                break
            _draw_task_row(draw, pad + 12, y, t, left_w - pad - 24, 58, done=True)
            y += row_h

    # --- Right: next 3 days ---
    for i in range(3):
        d = today + timedelta(days=i + 1)
        cx = right_x + i * (col_w + col_gap)
        _draw_panel_bg(draw, cx, body_y, col_w, body_h)
        day_label = d.strftime('%a %d')
        day_full = d.strftime('%A')
        draw.text((cx + 10, body_y + 8), day_label, fill=ACCENT, font=font(18))
        draw.text((cx + 10, body_y + 30), day_full, fill=TEXT_DIM, font=font(13))
        yy = body_y + 52
        day_tasks = upcoming.get(d, [])
        if not day_tasks:
            draw.text((cx + 10, yy), 'Nothing', fill=TEXT_DIM, font=font(15))
        for t in day_tasks:
            if yy + row_h > body_y + body_h - 6:
                break
            tme = str(t.get('time', '') or '')
            if tme:
                draw.text((cx + 10, yy), tme, fill=ACCENT, font=font(14))
                tx = cx + 10 + 52
            else:
                tx = cx + 10
            title = _truncate(draw, str(t.get('title', '')), font(15), col_w - (tx - cx) - 10)
            draw.text((tx, yy), title, fill=TEXT_BRIGHT, font=font(15))
            yy += row_h

    return img
