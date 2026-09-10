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


def _wrap(draw, text, f, max_w, max_lines=2):
    """Wrap text into at most max_lines lines that fit max_w, ellipsizing if clipped."""
    words = text.split()
    lines, cur = [], ''
    for wd in words:
        trial = (cur + ' ' + wd).strip()
        if not cur or draw.textlength(trial, font=f) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
            if len(lines) >= max_lines - 1:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if not lines:
        return ['']
    while draw.textlength(lines[-1], font=f) > max_w and len(lines[-1]) > 1:
        lines[-1] = lines[-1][:-1]
    joined = ' '.join(lines)
    if len(joined) < len(' '.join(words)) and len(lines) == max_lines:
        while draw.textlength(lines[-1] + '...', font=f) > max_w and len(lines[-1]) > 1:
            lines[-1] = lines[-1][:-1]
        lines[-1] += '...'
    return lines


def _circle_check(draw, cx, cy, r, done=False, overdue=False):
    """Popular-app style round checkbox. Filled + tick when done."""
    if done:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=GREEN)
        draw.line([(cx - r * 0.45, cy + r * 0.05), (cx - r * 0.05, cy + r * 0.45),
                   (cx + r * 0.5, cy - r * 0.4)], fill=(0, 0, 0), width=3)
    else:
        col = RED if overdue else ACCENT
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=3)


def _pill(draw, x, y, text, fg, bg, pad=8):
    """Draw a rounded tag pill, return its width."""
    tw = draw.textlength(text, font=font(16))
    draw.rounded_rectangle([x, y, x + tw + pad * 2, y + 24], radius=12, fill=bg)
    draw.text((x + pad, y + 3), text, fill=fg, font=font(16))
    return tw + pad * 2


TAG_COLORS = {
    'finance': (255, 210, 70),
    'health': (90, 230, 160),
    'business': (150, 170, 255),
    'lightcogswell': (190, 130, 255),
    'fieldcraft': (120, 200, 255),
}

def _tag_color(tags):
    for t in (tags or []):
        if str(t).lower() in TAG_COLORS:
            return TAG_COLORS[str(t).lower()]
    return PURPLE


def render_frame(w=1920, h=440):
    """Render the todo dashboard: big TODAY list + one consolidated upcoming card."""
    img = Image.new('RGB', (w, h), BG)
    draw = ImageDraw.Draw(img)

    for y in range(h):
        t = y / max(h - 1, 1)
        draw.line([(0, y), (w, y)], fill=_lerp_color((8, 12, 22), (4, 6, 12), t))

    draw = _draw_header(img, draw, w)

    today = date.today()
    draw.text((20, 12), 'TASKS', fill=ACCENT, font=font(24))
    stamp = today.strftime('%A, %b %d')
    draw.text((w - 20 - draw.textlength(stamp, font=font(20)), 14), stamp,
              fill=TEXT_DIM, font=font(20))

    tasks = _load_tasks()
    if tasks is None:
        draw.text((40, h // 2 - 20), 'No todo list yet.', fill=TEXT_DIM, font=font(24))
        draw.text((40, h // 2 + 14), 'Ask your agent to add tasks, or run todo-cli.py add "Task"',
                  fill=TEXT_DIM, font=font(18))
        return img

    overdue, today_pending, today_done, upcoming = _split_tasks(tasks, today)

    pad = 16
    body_y = 56
    body_h = h - body_y - 10
    left_w = 1120
    right_x = pad + left_w + 12
    right_w = w - right_x - pad

    # ---------- LEFT: TODAY (large) ----------
    _draw_panel_bg(draw, pad, body_y, left_w, body_h)
    draw.text((pad + 20, body_y + 12), 'TODAY', fill=ACCENT, font=font(26))
    open_count = len(today_pending) + len(overdue)
    cnt = f'{open_count} open'
    draw.text((pad + 20 + draw.textlength('TODAY', font=font(26)) + 18, body_y + 18),
              cnt, fill=TEXT_DIM, font=font(18))

    row_h = 50
    y = body_y + 48
    x0 = pad + 20
    row_w = left_w - 44
    max_bottom = body_y + body_h - 8

    def draw_row(task, overdue=False, done=False):
        nonlocal y
        title_f = font(30)
        tme = str(task.get('time', '') or '')
        col = RED if overdue else (TEXT_DIM if done else TEXT_BRIGHT)
        _circle_check(draw, x0 + 13, y + 15, 12, done=done, overdue=overdue)
        tx = x0 + 40
        if tme:
            draw.text((tx, y + 2), tme, fill=(TEXT_DIM if done else ACCENT), font=font(24))
            tx += 88
        # tags pill on the right
        tags = task.get('tags') or []
        pill_w = 0
        if tags:
            pill_w = _pill(draw, x0 + row_w - 150, y + 2, '#' + str(tags[0]),
                           (12, 16, 26), _tag_color(tags)) + 12
        title = _truncate(draw, str(task.get('title', '')), title_f,
                          row_w - (tx - x0) - pill_w)
        draw.text((tx, y), title, fill=col, font=title_f)
        if overdue:
            draw.text((x0 + row_w - 106, y + 6), 'OVERDUE', fill=RED, font=font(16))
        y += row_h

    for t in overdue:
        if y + row_h > max_bottom:
            break
        draw_row(t, overdue=True)
    for t in today_pending:
        if y + row_h > max_bottom:
            break
        draw_row(t)
    if today_done and y + 30 < max_bottom:
        y += 6
        draw.text((x0, y), 'COMPLETED', fill=TEXT_DIM, font=font(16))
        y += 26
        for t in today_done:
            if y + row_h - 14 > max_bottom:
                break
            draw_row(t, done=True)

    # ---------- RIGHT: UPCOMING, one consolidated card ----------
    _draw_panel_bg(draw, right_x, body_y, right_w, body_h)
    draw.text((right_x + 20, body_y + 12), 'UPCOMING', fill=ACCENT, font=font(26))
    draw.text((right_x + 20 + draw.textlength('UPCOMING', font=font(26)) + 18,
               body_y + 18), 'next 3 days', fill=TEXT_DIM, font=font(18))

    rows = []
    for i in range(3):
        d = today + timedelta(days=i + 1)
        for t in upcoming.get(d, []):
            rows.append((d, t))

    ry = body_y + 46
    r_h = 34
    due_w = 96
    for d, t in rows:
        if ry + r_h > body_y + body_h - 8:
            break
        draw.ellipse([right_x + 24, ry + 9, right_x + 32, ry + 17],
                     fill=_tag_color(t.get('tags')))
        tme = str(t.get('time', '') or '')
        label = str(t.get('title', ''))
        if tme:
            label = f'{tme}  {label}'
        f = font(22)
        title = _truncate(draw, label, f, right_w - 60 - due_w)
        draw.text((right_x + 44, ry), title, fill=TEXT_BRIGHT, font=f)
        due_txt = d.strftime('%a %-m/%-d') if os.name != 'nt' else d.strftime('%a %#m/%#d')
        draw.text((right_x + right_w - 20 - draw.textlength(due_txt, font=font(18)), ry + 3),
                  due_txt, fill=ACCENT_DIM, font=font(18))
        ry += r_h

    if not rows:
        draw.text((right_x + 20, ry), 'Nothing scheduled', fill=TEXT_DIM, font=font(22))

    return img
