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


# --- Firework celebration on task close -------------------------------
# When a task that was open flips to done while the display is showing it,
# play a full fireworks show (~3s: rockets rising, flash + shockwave
# detonations, flickering trails, embers, grand finale), then remove
# the closed task from todo.json.
import math
import random

_FW_DUR = 3.0
_FW = {
    'open_keys': None,
    'anim_start': None,
    'dur': _FW_DUR,
    'rockets': [],   # {t0, dur, x0, x1, y1, seed}
    'bursts': [],    # {t0, x, y, col, parts:[...], ring}
    'closed': [],
    'prev': {},      # particle trail cache: (bi,pi) -> (x, y)
}
_GOLD = (255, 210, 70)
_GOLD_BRIGHT = (255, 235, 140)
_FW_PALETTES = [
    [(255, 210, 70), (255, 235, 140), (255, 250, 235)],
    [(0, 240, 255), (120, 230, 255), (255, 255, 255)],
    [(255, 90, 160), (255, 150, 200), (255, 240, 245)],
    [(0, 255, 150), (150, 255, 200), (245, 255, 250)],
    [(190, 130, 255), (230, 190, 255), (250, 245, 255)],
]

def _task_key(t):
    return (str(t.get('title', '')).strip(),
            str(t.get('due', '')).strip(),
            str(t.get('time', '')).strip())

def frame_interval():
    """Fast frames while the celebration is running."""
    return 1/30 if _FW['anim_start'] is not None else 1/2

def _build_show(w, h):
    """Pre-compute rockets + bursts for the whole show."""
    rockets, bursts = [], []
    # four rockets on 0.35s cadence so multiple bursts are alive together
    for i, t0 in enumerate((0.0, 0.35, 0.70, 1.05)):
        x1 = random.uniform(0.20, 0.80) * w
        y1 = random.uniform(0.18, 0.40) * h
        rockets.append({
            't0': t0, 'dur': 0.45,
            'x0': x1 + random.uniform(-70, 70),
            'x1': x1, 'y1': y1,
            'seed': random.random() * 10,
        })
        _add_burst(bursts, x1, y1, t0 + 0.45,
                   palette_idx=i % len(_FW_PALETTES), n=150)
    # grand finale: triple burst, big + staggered
    fx, fy = w / 2, h * 0.30
    _add_burst(bursts, fx - w * 0.17, h * 0.33, 2.00,
               palette_idx=2, n=160)
    _add_burst(bursts, fx, h * 0.26, 2.10,
               palette_idx=0, n=230)
    _add_burst(bursts, fx + w * 0.17, h * 0.35, 2.20,
               palette_idx=1, n=160)
    _FW['rockets'] = rockets
    _FW['bursts'] = bursts
    _FW['prev'] = {}

def _add_burst(bursts, x, y, t0, palette_idx, n):
    pal = _FW_PALETTES[palette_idx]
    parts = []
    for _ in range(n):
        main = random.random() < 0.72
        parts.append({
            'ang': random.uniform(0, 2 * math.pi),
            'sp': random.uniform(120, 460) if main else random.uniform(70, 190),
            'life': (random.uniform(1.0, 1.9) if main
                     else random.uniform(1.8, 2.8)),
            'r': random.uniform(1.8, 3.8),
            'col': random.choice(pal),
            'ph': random.random() * 10,
            'ember': not main,
        })
    bursts.append({'t0': t0, 'x': x, 'y': y, 'col': pal[0],
                   'parts': parts, 'ring': t0})

def _rocket_pos(rk, t, bottom):
    """Rocket position at show-time t (None if not yet launched/finished)."""
    if t < rk['t0'] or t > rk['t0'] + rk['dur']:
        return None
    f = (t - rk['t0']) / rk['dur']
    ease = f * f
    x = rk['x0'] + (rk['x1'] - rk['x0']) * ease \
        + math.sin(f * 9 + rk['seed']) * 6 * (1 - f)
    y = bottom + (rk['y1'] - bottom) * ease
    return x, y, f

def _draw_celebration(img, t):
    from PIL import ImageDraw, ImageFilter, ImageChops
    W, H = img.size
    h_bottom = H + 12
    dim_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dim_layer)
    glow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    parts = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    od = ImageDraw.Draw(parts)

    # dim the board into night so the sky reads (hold, fade out last 0.5s)
    if t < 0.15:
        dim = int(205 * (t / 0.15))
    elif t < 2.5:
        dim = 205
    else:
        dim = int(205 * max(0.0, (3.0 - t) / 0.5))
    dd.rectangle([0, 0, W, H], fill=(0, 0, 0, dim))
    img.paste(dim_layer, (0, 0), dim_layer)

    # opening gold flash (additive)
    if t < 0.18:
        a = int(190 * (1 - t / 0.18))
        gd.rectangle([0, 0, W, H], fill=(255, 230, 150, a))

    # ── rockets ──
    for rk in _FW['rockets']:
        pos = _rocket_pos(rk, t, h_bottom)
        if pos is None:
            continue
        x, y, f = pos
        core = max(2.0, 4.5 * (1 - f * 0.5))
        od.ellipse([x - core, y - core, x + core, y + core],
                   fill=(255, 245, 210, 255))
        od.ellipse([x - core * 2, y - core * 2,
                    x + core * 2, y + core * 2],
                   fill=(255, 200, 90, 120))
        tx = x - (rk['x1'] - rk['x0']) * 0.06
        od.line([(x, y), (tx, y + 46 + random.uniform(-10, 10))],
                fill=(255, 210, 120, 220), width=3)
        od.line([(x, y), (tx * 0.5 + x * 0.5, y + 80)],
                fill=(255, 150, 60, 90), width=2)

    # ── bursts ──
    for bi, b in enumerate(_FW['bursts']):
        bt = t - b['t0']
        if bt < 0:
            continue
        # white-hot core flash (additive)
        if bt < 0.35:
            cr = 90 * (bt / 0.35) + 18
            for k in (4, 3, 2, 1):
                gd.ellipse([b['x'] - cr / k, b['y'] - cr / k,
                            b['x'] + cr / k, b['y'] + cr / k],
                           fill=(255, 250, 230, int(220 / k * (1 - bt / 0.35))))
        # shockwave ring
        if bt < 0.5:
            rr = 14 + bt * 340
            ra = int(190 * (1 - bt / 0.5))
            od.ellipse([b['x'] - rr, b['y'] - rr,
                        b['x'] + rr, b['y'] + rr],
                       outline=(255, 255, 255, ra), width=2)
        # particles
        for pi, p in enumerate(b['parts']):
            decay = 1.15
            vt = math.exp(-decay * bt)
            reach = p['sp'] / decay * (1 - vt)
            x = b['x'] + math.cos(p['ang']) * reach
            y = b['y'] + math.sin(p['ang']) * reach \
                + 0.5 * 240 * bt * bt
            if y > H + 10:
                continue
            frac = bt / p['life']
            if frac >= 1.0:
                continue
            flick = 0.6 + 0.4 * math.sin(bt * (20 + p['ph']) + p['ph'])
            if random.random() < (0.28 if p['ember'] else 0.07):
                flick *= 0.35
            a = int(255 * max(0.0, flick) * min(1.0, (1 - frac) * 1.7))
            if a < 20:
                continue
            r = max(1.5, p['r'] * (1 - 0.45 * frac))
            col = p['col']
            key = (bi, pi)
            prev = _FW['prev'].get(key)
            if prev is not None and frac < 0.9:
                od.line([prev, (x, y)], fill=col + (a // 2,), width=2)
            od.ellipse([x - r, y - r, x + r, y + r], fill=col + (a,))
            if frac < 0.3:
                gd.ellipse([x - r * 2.2, y - r * 2.2,
                            x + r * 2.2, y + r * 2.2],
                           fill=(255, 255, 240, a // 3))
            _FW['prev'][key] = (x, y)

    # ── TASK COMPLETE banner (additive glow + solid text) ──
    if t < 2.5:
        pop = min(1.0, t / 0.16)
        size = int(58 * (0.6 + 0.4 * pop))
        f = font(size)
        label = 'TASK COMPLETE'
        tw = od.textlength(label, font=f)
        bx = (W - tw) // 2
        by = H // 2 - size // 2 - 6
        a = 255 if t < 2.0 else int(255 * (2.5 - t) / 0.5)
        gd.text((bx, by), label, fill=_GOLD + (200,), font=f)
        od.text((bx + 3, by + 3), label, fill=(30, 20, 0, a), font=f)
        od.text((bx, by), label, fill=_GOLD_BRIGHT + (a,), font=f)

    # additive passes: composite layers over black, then screen-blend so
    # colors glow instead of dimming against the board
    def _additive(layer):
        base = Image.new('RGB', img.size, (0, 0, 0))
        base.paste(layer, (0, 0), layer)
        return ImageChops.screen(img, base)

    img = _additive(glow)
    img = _additive(parts)
    return img

def _remove_closed():
    """Delete the celebrated tasks from todo.json (atomic write)."""
    if not _FW['closed']:
        return
    try:
        with open(TODOFILE) as f:
            data = json.load(f)
        tasks = data.get('tasks', [])
        kept = [t for t in tasks
                if not (t.get('done') and _task_key(t) in _FW['closed'])]
        if len(kept) == len(tasks):
            return
        data['tasks'] = kept
        tmp = TODOFILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, TODOFILE)
        try:
            os.utime(TODOFILE, (time.time() + 1, time.time() + 1))
        except Exception:
            pass
        _cache['mtime'] = None
    except Exception:
        pass

def _fireworks_tick(tasks, img, w, h):
    """Detect open->done transitions, run the animation, remove tasks.
    Returns True if the frame should be shown as-is (animation inactive)."""
    now_open = set()
    for t in tasks:
        if not t.get('done'):
            now_open.add(_task_key(t))
    newly_closed = []
    if _FW['open_keys'] is not None:
        for t in tasks:
            if t.get('done') and _task_key(t) in _FW['open_keys'] \
                    and _task_key(t) not in newly_closed:
                newly_closed.append(_task_key(t))
    _FW['open_keys'] = now_open
    if _FW['anim_start'] is None and newly_closed:
        _FW['anim_start'] = time.monotonic()
        _FW['closed'] = newly_closed
        _build_show(w, h)
    if _FW['anim_start'] is None:
        return img
    t = time.monotonic() - _FW['anim_start']
    img = _draw_celebration(img, t)
    if t >= _FW['dur']:
        _remove_closed()
        _FW['anim_start'] = None
        _FW['rockets'] = []
        _FW['bursts'] = []
        _FW['closed'] = []
        _FW['prev'] = {}
    return img


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

    # Firework celebration: detect tasks that just closed, animate, remove
    img = _fireworks_tick(tasks, img, w, h)

    return img
