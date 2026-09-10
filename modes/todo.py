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


# --- Close animations -------------------------------------------------
# Per-task close: the row scratches off left -> right into a cloud of smoke
# (scratch line + rising smoke puffs in the quarter-res light buffer).
# Multiple tasks closed at once scratch simultaneously. Once the scratch
# finishes, the task is removed from todo.json and the rows below jump up
# (crossfade reflow). The full fireworks show plays ONLY when everything
# due today is completed ("DAY CLEARED"), not on individual task closes.
import math
import random

import numpy as _np
from scenes import engine as _engine

_FW_DUR = 3.5          # fireworks show length
_FW_SCRATCH_DUR = 1.4  # scratch-off show length (line 0.55s + smoke tail)
_FW_SCRATCH = 0.55
_FW_SETTLE = 0.28
_FW = {
    'open_keys': None,
    'anim_start': None,
    'dur': _FW_DUR,
    'show_type': None,   # 'scratch' | 'fireworks'
    'segments': [],      # {x, y, w, h, t_delay, removed_key}
    'fw_text': '',
    't_last_fw_end': 0.0,
    'prev': {},          # particle trail cache: (bi, pi) -> (x, y)
    'board_cache': None,   # (mtime, date, image) -- static board, reused
    'ghost': None,         # previous frame's light buffer (motion trails)
    'smoke_ghost': None,   # previous frame's smoke light buffer
    'smoke_start': None,   # monotonic time of the smoke trail timeline
    'base': None,          # (mtime, image) board snapshot for the reflow blend
    'pre_tasks': None,     # synthetic pre-close task list (scratch board)
    'day_was_open': False, # due-today set was open before the last scratch
    'removed_keys': set(),  # keys of the rows the show should remove
    'spr_arr': {},         # id(sprite) -> alpha-weighted float32 array
    'wash': None,
    'flash': None,
    'rhead': None,
    'rhalo': None,
    'bglow': None,
    'scratch_line': None,
    'smoke_spr': None,
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

# Cached glow sprites from the shared scenes engine
_FW_SPRITE_CACHE = {}
def _glow(radius, color, peak=200):
    radius = int(max(2, round(radius)))
    key = (radius, color, peak)
    if key not in _FW_SPRITE_CACHE:
        _FW_SPRITE_CACHE[key] = _engine.glow_sprite(radius, color, alpha_peak=peak)
    return _FW_SPRITE_CACHE[key]

def _stamp(dst, x, y, sprite, scale=1.0):
    """Stamp a glow sprite centered at (x, y) with brightness scaling."""
    if scale >= 0.999:
        _engine.stamp_glow(dst, int(x), int(y), sprite)
        return
    arr = _np.array(sprite, dtype=_np.float32)
    if scale > 1.0:
        arr[..., :3] = _np.clip(arr[..., :3] * min(1.6, scale), 0, 255)
    arr[..., 3] = (arr[..., 3] * min(scale, 1.0)).astype(_np.uint8)
    _engine.stamp_glow(dst, int(x), int(y),
                       Image.fromarray(arr.astype(_np.uint8), 'RGBA'))

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
    # Pre-compute per-burst arrays + quantized sprites once (per frame
    # these used to be rebuilt: 9 bursts x 4 list->array + ~140 sprite
    # generations was the main cost).
    for b in bursts:
        parts = b['parts']
        n = len(parts)
        b['n'] = n
        b['ang'] = _np.array([p['ang'] for p in parts], dtype=_np.float32)
        b['sp'] = _np.array([p['sp'] for p in parts], dtype=_np.float32)
        b['life'] = _np.array([p['life'] for p in parts], dtype=_np.float32)
        b['ph'] = _np.array([p['ph'] for p in parts], dtype=_np.float32)
        b['ember'] = _np.array([p['ember'] for p in parts])
        # quarter-res buffer radius, quantized to 5 tiers -> small sprite
        # cache (id-keyed) reused every frame
        tiers = (3, 4, 5, 6, 7)
        b['rqs'] = []
        b['cols'] = []
        for p in parts:
            t = min(4, max(0, int((p['r'] - 1.8) / 2.0 * 5)))
            b['rqs'].append(tiers[t])
            b['cols'].append(p['col'])
        b['sprs'] = [_fw_sprite_arr(_glow(r, c, 230)) for r, c in
                     zip(b['rqs'], b['cols'])]

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

def _fw_sprite_arr(sprite):
    """Alpha-weighted float RGB array of a sprite (cached) -- the form used
    for direct additive splatting into the light buffer."""
    key = id(sprite)
    a = _FW['spr_arr'].get(key)
    if a is None:
        s = _np.asarray(sprite, dtype=_np.float32)
        a = s[..., :3] * (s[..., 3:4] / 255.0)
        _FW['spr_arr'][key] = a
    return a

def _splat(buf, x, y, spr, bright):
    """Additively splat a sprite (preweighted float array) into buf at
    (x, y) with brightness. One vectorized slice add -- no PIL round trip."""
    if bright <= 0.02:
        return
    bright = min(1.6, bright)
    BH, BW = buf.shape[:2]
    h, w = spr.shape[:2]
    x0 = int(x - w / 2)
    y0 = int(y - h / 2)
    dx0 = max(0, x0)
    dy0 = max(0, y0)
    dx1 = min(BW, x0 + w)
    dy1 = min(BH, y0 + h)
    if dx1 <= dx0 or dy1 <= dy0:
        return
    buf[dy0:dy1, dx0:dx1] += spr[dy0 - y0:dy1 - y0, dx0 - x0:dx1 - x0] * bright

def _splat_line(buf, x0, y0, x1, y1, spr, bright, steps=6):
    """Glowing line as a chain of overlapping sprite splats (rocket tail)."""
    for s in range(steps):
        f = s / (steps - 1)
        _splat(buf, x0 + (x1 - x0) * f, y0 + (y1 - y0) * f,
               spr, bright * (1.0 - 0.55 * f))

def _splat_circle(buf, cx, cy, r, spr, bright, steps=40):
    """Glowing ring as a ring of sprite splats (shockwave)."""
    for s in range(steps):
        a = 2 * math.pi * s / steps
        _splat(buf, cx + math.cos(a) * r, cy + math.sin(a) * r,
               spr, bright)

def _draw_scratch(img, t=None):
    """Scratch-off: each closed row erases left -> right under a bright line
    with rising smoke (light buffer, same splat technique as the show).
    The row is erased before the task leaves todo.json, so the rows below
    jump up the moment the removal lands."""
    if t is None:
        if _FW['anim_start'] is None:
            return img
        t = time.monotonic() - _FW['anim_start']
    W, H = img.size
    board = img
    sw, sh = W // 4, H // 4
    buf = _np.zeros((sh, sw, 3), dtype=_np.float32)
    ghost = _FW['ghost']
    if ghost is not None and ghost.shape == (sh, sw, 3):
        buf += ghost * 0.78        # smoke/motion trails
    line_spr = _fw_sprite_arr(_FW['scratch_line'])
    smoke_spr = _fw_sprite_arr(_FW['smoke_spr'])
    d2 = ImageDraw.Draw(board)
    lines = []                     # (line_x, cy) for rows still mid-scratch
    for seg in _FW['segments']:
        x, y, w, h = seg['x'], seg['y'], seg['w'], seg['h']
        bt = max(0.0, t - seg.get('t_delay', 0.0))
        f = bt / _FW_SCRATCH
        f = max(0.0, min(1.0, f))
        if f <= 0.0:
            continue
        # 1. erase: repaint the swept region with the panel gradient
        line_x = int(x + w * f)
        for yy in range(int(y), int(y + h)):
            tt = (yy - 56) / max(1, (440 - 56 - 10) - 1)
            c = _lerp_color((14, 18, 30), (8, 11, 20), tt)
            d2.line([(x, yy), (line_x, yy)], fill=c)
        cy = int(y + h / 2)
        # 2. glow splat where the line is moving (line itself is drawn
        #    AFTER the composite so the smoke can't wash it out)
        if f < 1.0:
            lines.append((line_x, cy))
            _splat(buf, line_x / 4.0, cy / 4.0, line_spr, 1.1)
            emit = min(1.0, bt / 0.1)
        else:
            # settled: puffs keep drifting from where the line ended
            emit = max(0.0, 1.0 - (t - _FW_SCRATCH) / 0.5)
            if emit <= 0.0:
                continue
            line_x = x + w
        # 3. smoke: fresh puffs each frame along an up-and-out drift; the
        #    ghost buffer smears them into a dissipating cloud
        for k in range(7):
            r1, r2, r3 = (random.random() for _ in range(3))
            sx = line_x + (r1 - 0.4) * (18 + 40 * f) + 14 * f
            sy = cy + (r2 - 0.5) * 26 - 8 - 46 * f - r3 * 30
            bright = emit * (0.28 + 0.3 * r1) * (1.0 - 0.5 * f)
            _splat(buf, sx / 4.0, sy / 4.0, smoke_spr, bright)
    # upscale + one additive composite (identical path to the show)
    light = _np.clip(buf, 0, 255).astype(_np.uint8)
    light_img = Image.fromarray(light, 'RGB').resize((W, H),
                                                     Image.Resampling.BILINEAR)
    b3 = _np.asarray(board, dtype=_np.float32)
    out = _np.clip(b3 + _np.asarray(light_img, dtype=_np.float32), 0, 255)
    board = Image.fromarray(out.astype(_np.uint8), 'RGB')
    _FW['ghost'] = buf
    # 4. crisp scratch lines on top of the composite
    if lines:
        d3 = ImageDraw.Draw(board)
        for line_x, cy in lines:
            d3.line([(line_x - 1, cy - 17), (line_x - 1, cy + 17)],
                    fill=(90, 230, 255), width=3)
            d3.line([(line_x + 2, cy - 12), (line_x + 2, cy + 12)],
                    fill=(230, 250, 255), width=1)
    return board

def _draw_fireworks(img, t=None):
    if t is None:
        if _FW['anim_start'] is None:
            return img
        t = time.monotonic() - _FW['anim_start']
    W, H = img.size
    h_bottom = H + 12
    board = img

    # 1. dim the board into night so the sky reads
    if t < 0.15:
        dark = t / 0.15
    elif t < 2.8:
        dark = 1.0
    else:
        dark = max(0.0, (3.5 - t) / 0.7)
    if dark < 1.0:
        board = Image.blend(Image.new('RGB', (W, H), (0, 0, 0)), board, 1 - dark)
    else:
        board = Image.new('RGB', (W, H), (0, 0, 0))
    _banner = t < 2.8

    # ---- light buffer: QUARTER-res float RGB, splatted purely in numpy.
    # The 4x upscale turns every glow into a very soft wide bloom for free,
    # and a quarter-res buffer is 16x cheaper to upscale+composite than
    # full-res -- the same "splat cached sprites" trick the scenes use,
    # vectorized so ~500 particles cost slice adds, not 500 PIL stamps.
    # (Light uses buffer coords = full/4; rocket tail + shockwave ring are
    #  drawn full-res directly on the board below.)
    sw, sh = W // 4, H // 4
    buf = _np.zeros((sh, sw, 3), dtype=_np.float32)
    ghost = _FW['ghost']
    if ghost is not None and ghost.shape == (sh, sw, 3):
        buf += ghost * 0.72          # motion trails: last frame's light decays
    else:
        ghost = _np.zeros((sh, sw, 3), dtype=_np.float32)

    wash = _fw_sprite_arr(_FW['wash'])
    flash = _fw_sprite_arr(_FW['flash'])
    rhead = _fw_sprite_arr(_FW['rhead'])
    rhalo = _fw_sprite_arr(_FW['rhalo'])
    tail = _fw_sprite_arr(_glow(4, (255, 215, 120), 200))
    ring = _fw_sprite_arr(_glow(3, (255, 255, 255), 220))

    # 2. opening golden wash
    if t < 0.5:
        _splat(buf, sw / 2, sh / 2, wash, (1 - t / 0.5) * 0.9)

    # 3. rockets (glow head + flickering tail, all in the light buffer)
    for rk in _FW['rockets']:
        pos = _rocket_pos(rk, t, h_bottom)
        if pos is None:
            continue
        x, y, f = pos
        _splat(buf, x / 4.0, y / 4.0, rhead, 1.3)
        _splat(buf, x / 4.0, y / 4.0, rhalo, 1.0)
        wob = math.sin(t * 42 + rk['seed']) * 4
        _splat_line(buf, x / 4.0, y / 4.0, (x - wob) / 4.0, (y + 56) / 4.0,
                    tail, 1.0)

    # 4. bursts (arrays + sprites pre-computed at show start)
    for bi, b in enumerate(_FW['bursts']):
        bt = t - b['t0']
        if bt < 0:
            continue
        bx, by = b['x'] / 4.0, b['y'] / 4.0
        if bt < 0.30:
            _splat(buf, bx, by, flash, (1 - bt / 0.30) * 1.6)
        if bt < 0.5:
            rr = int(16 + bt * 380)
            ra = int(170 * (1 - bt / 0.5))
            if ra > 4:
                _splat_circle(buf, bx, by, rr / 4.0, ring,
                              ra / 170.0, steps=44)
        ang, sp, life, ph, ember = b['ang'], b['sp'], b['life'], b['ph'], b['ember']
        reach = sp / 1.15 * (1.0 - _np.exp(-1.15 * bt))
        px = b['x'] + _np.cos(ang) * reach
        py = b['y'] + _np.sin(ang) * reach + 0.5 * 240 * bt * bt
        frac = bt / life
        flick = 0.62 + 0.38 * _np.sin(bt * (20 + ph) + ph)
        rnd = _np.random.random(len(life))
        flick[rnd < 0.20] *= 0.35
        flick[(rnd < 0.06) & ~ember] *= 0.35
        bright = flick * _np.minimum(1.0, (1 - frac) * 1.8)
        alive = (frac < 1.0) & (bright > 0.05)
        if not alive.any():
            continue
        sprs = b['sprs']
        prev = _FW['prev'].get(bi)
        if prev is None:
            prev = (None,) * b['n']
        for i in _np.nonzero(alive)[0]:
            _splat(buf, px[i] / 4.0, py[i] / 4.0, sprs[i], float(bright[i]))
            p0 = prev[i]
            if p0 is not None and frac[i] < 0.85 and bright[i] > 0.15:
                _splat(buf, (px[i] + p0[0]) / 8.0, (py[i] + p0[1]) / 8.0,
                       sprs[i], float(bright[i]) * 0.45)
        newprev = list(prev)
        for i in _np.nonzero(alive)[0]:
            newprev[i] = (float(px[i]), float(py[i]))
        _FW['prev'][bi] = tuple(newprev)

    # 6. banner glow goes INTO the light buffer (it is a glow -- one
    #    composite pass total, no extra full-res asarrays)
    _banner = t < 2.8
    if _banner:
        pop = min(1.0, t / 0.16)
        a = 255 if t < 2.2 else int(255 * (2.8 - t) / 0.6)
        _splat(buf, sw / 2, sh / 2, _fw_sprite_arr(_FW['bglow']),
               (a / 255) * pop)

    # 7. upscale (the 4x bilinear softens the light into a wide free bloom)
    #    + ONE additive composite. When the board is fully black (the bulk
    #    of the show) the light IS the frame -- skip the board add entirely.
    light = _np.clip(buf, 0, 255).astype(_np.uint8)
    light_img = Image.fromarray(light, 'RGB').resize((W, H),
                                                     Image.Resampling.BILINEAR)
    if dark >= 1.0:
        board = light_img
    else:
        b3 = _np.asarray(board, dtype=_np.float32)
        out = _np.clip(b3 + _np.asarray(light_img, dtype=_np.float32), 0, 255)
        board = Image.fromarray(out.astype(_np.uint8), 'RGB')
    _FW['ghost'] = buf

    # 8. banner text (full-res, crisp, ~1ms)
    if _banner:
        pop = min(1.0, t / 0.16)
        size = int(54 * (0.6 + 0.4 * pop))
        f = font(size)
        label = _FW.get('fw_text') or 'TASK COMPLETE'
        d2 = ImageDraw.Draw(board)
        tw = d2.textlength(label, font=f)
        bx = (W - tw) // 2
        by = H // 2 - size // 2 - 6
        d2.text((bx + 3, by + 3), label, fill=(25, 18, 0), font=f)
        d2.text((bx, by), label, fill=_GOLD_BRIGHT, font=f)

    return board

def _draw_celebration(img, t=None):
    """Dispatch on the active show type."""
    if _FW['show_type'] == 'scratch':
        return _draw_scratch(img, t)
    return _draw_fireworks(img, t)

def _remove_closed():
    """Delete the closed tasks from todo.json (atomic write)."""
    keys = _FW.get('removed_keys') or set()
    if not keys:
        return
    try:
        with open(TODOFILE) as f:
            data = json.load(f)
        tasks = data.get('tasks', [])
        kept = [t for t in tasks
                if not (t.get('done') and _task_key(t) in keys)]
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
    _FW['removed_keys'] = set()
    _FW['base'] = None

def _today_open_count(tasks):
    """Count open tasks due today or overdue (the 'due today' set)."""
    today = date.today()
    n = 0
    for t in tasks:
        if t.get('done'):
            continue
        d = _parse_date(t.get('due'))
        if d <= today:
            n += 1
    return n

def _pre_close_list(tasks, keys):
    """Copy of the task list with the given just-closed rows flipped back
    to open -- the board to draw while they are being scratched off."""
    pre = []
    for t in tasks:
        tt = dict(t)
        if tt.get('done') and _task_key(tt) in keys:
            tt['done'] = False
        pre.append(tt)
    return pre

def _start_scratch(tasks, closed, closed_keys, w, h):
    """Begin a scratch-off show for the closed rows. The board is drawn
    from the pre-close list so the rows stay in place while they are
    erased; the file write + row jump-up happen when the show ends."""
    now = time.monotonic()
    n = _today_open_count(tasks)
    _FW['show_type'] = 'scratch'
    _FW['dur'] = _FW_SCRATCH_DUR
    _FW['anim_start'] = now
    _FW['fw_text'] = ''
    _FW['day_was_open'] = (n + len(closed)) > 0
    _FW['pre_tasks'] = _pre_close_list(tasks, closed_keys)
    _FW['segments'] = _scratch_segs(_FW['pre_tasks'], closed_keys)
    _FW['board_cache'] = None
    _FW['ghost'] = None
    _FW['smoke_ghost'] = None
    _FW['smoke_start'] = now
    _FW['base'] = None
    return True

def _detect(tasks, w, h):
    """Detect open->done transitions and pick the celebration to play:
    'scratch' for one-or-more task closes (all at once), 'fireworks'
    only when the last task due today is completed (DAY CLEARED).
    Returns True while a celebration is playing."""
    now = time.monotonic()
    today_d = date.today()
    if _FW['anim_start'] is not None and now - _FW['anim_start'] >= _FW['dur']:
        if _FW['show_type'] == 'scratch':
            _remove_closed()
        day_was_open = _FW['day_was_open']
        _FW['anim_start'] = None
        _FW['show_type'] = None
        _FW['segments'] = []
        _FW['fw_text'] = ''
        _FW['rockets'] = []
        _FW['bursts'] = []
        _FW['prev'] = {}
        _FW['board_cache'] = None
        _FW['ghost'] = None
        _FW['smoke_ghost'] = None
        _FW['smoke_start'] = None
        _FW['base'] = None
        _FW['pre_tasks'] = None
        _FW['removed_keys'] = set()
        # Day cleared: the scratch removal just landed and nothing due
        # today is left open -> the fireworks are the reward for the
        # whole day, not for this single task close.
        tasks2 = _load_tasks() or []
        if (day_was_open and _today_open_count(tasks2) == 0
                and now - _FW['t_last_fw_end'] > 6.0):
            _FW['show_type'] = 'fireworks'
            _FW['dur'] = _FW_DUR
            _FW['anim_start'] = now
            _FW['fw_text'] = 'DAY CLEARED'
            _FW['t_last_fw_end'] = now
            _build_show(w, h)
            return True
        return False
    if _FW['wash'] is None:
        _FW['wash'] = _glow(300, _GOLD, 235)
        _FW['flash'] = _glow(26, (255, 255, 240), 255)
        _FW['rhead'] = _glow(5, (255, 250, 235), 255)
        _FW['rhalo'] = _glow(13, (255, 200, 90), 140)
        _FW['bglow'] = _glow(150, _GOLD, 170)
        _FW['scratch_line'] = _glow(6, (255, 255, 250), 255)
        _FW['smoke_spr'] = _glow(22, (190, 195, 205), 60)
    now_open = set()
    for t in tasks:
        if not t.get('done'):
            now_open.add(_task_key(t))
    if _FW['anim_start'] is None:
        closed = []
        if _FW['open_keys'] is not None:
            for t in tasks:
                if (t.get('done') and _task_key(t) in _FW['open_keys']
                        and _parse_date(t.get('due')) <= today_d):
                    closed.append(t)
        _FW['open_keys'] = now_open
        if closed:
            _FW['removed_keys'] = set(_task_key(t) for t in closed)
            return _start_scratch(tasks, closed, _FW['removed_keys'], w, h)
        return False
    if _FW['show_type'] == 'scratch':
        # another close landed mid-show -> merge it into the running
        # scratch (all rows scratch simultaneously, left-to-right)
        closed = []
        if _FW['open_keys'] is not None:
            for t in tasks:
                if (t.get('done') and _task_key(t) in _FW['open_keys']
                        and _parse_date(t.get('due')) <= today_d):
                    closed.append(t)
        _FW['open_keys'] = now_open
        if closed:
            new_keys = set(_task_key(t) for t in closed)
            _FW['removed_keys'] |= new_keys
            _FW['day_was_open'] = True
            return _start_scratch(tasks, closed, _FW['removed_keys'], w, h)
    return True

def _scratch_segs(tasks, closed_keys):
    """Screen-row positions of the rows about to be scratched. The row
    index in the (pre-close) due-today order is the actual on-screen
    position; every row scratches at the same time (t_delay 0)."""
    today = date.today()
    overdue, today_pending, _, _ = _split_tasks(tasks, today)
    rows = list(overdue) + list(today_pending)
    pad = 16
    body_y = 56
    row_h = 50
    x0 = pad + 20
    row_w = 1120 - 44
    segs = []
    for i, t in enumerate(rows):
        if _task_key(t) not in closed_keys:
            continue
        segs.append({
            'x': x0, 'y': body_y + 48 + i * row_h - 3,
            'w': row_w, 'h': row_h, 't_delay': 0.0,
        })
    return segs

def render_frame(w=1920, h=440):
    """Render the todo dashboard: big TODAY list + one consolidated upcoming card."""
    tasks = _load_tasks()
    show = _detect(tasks, w, h) if tasks is not None else False

    # While a show plays the board is static (the file only changes at the
    # close moment) -> draw it once and reuse the cached copy every frame.
    mtime = None
    if tasks is not None:
        try:
            mtime = os.path.getmtime(TODOFILE)
        except OSError:
            pass
    today = date.today()
    cached = _FW['board_cache']
    if (show and cached is not None and mtime is not None
            and cached[0] == mtime and cached[1] == today):
        return _draw_celebration(cached[2].copy())

    img = Image.new('RGB', (w, h), BG)
    draw = ImageDraw.Draw(img)

    for y in range(h):
        t = y / max(h - 1, 1)
        draw.line([(0, y), (w, y)], fill=_lerp_color((8, 12, 22), (4, 6, 12), t))

    draw = _draw_header(img, draw, w)

    draw.text((20, 12), 'TASKS', fill=ACCENT, font=font(24))
    stamp = today.strftime('%A, %b %d')
    draw.text((w - 20 - draw.textlength(stamp, font=font(20)), 14), stamp,
              fill=TEXT_DIM, font=font(20))

    if tasks is None:
        draw.text((40, h // 2 - 20), 'No todo list yet.', fill=TEXT_DIM, font=font(24))
        draw.text((40, h // 2 + 14), 'Ask your agent to add tasks, or run todo-cli.py add "Task"',
                  fill=TEXT_DIM, font=font(18))
        return img

    # While a scratch-off show plays the board is drawn from the pre-close
    # list: the closed rows stay in their live positions (still 'open')
    # until the scratch finishes, then the removal lands and the rows
    # below jump up on the next frame.
    board_tasks = tasks
    if _FW['show_type'] == 'scratch' and _FW['pre_tasks'] is not None:
        board_tasks = _FW['pre_tasks']
    overdue, today_pending, today_done, upcoming = _split_tasks(board_tasks, today)

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

    # Firework celebration: the board was just drawn (it is static for the
    # duration of the show) -> cache it, then composite the sky over it.
    if show:
        _FW['board_cache'] = (mtime, today, img)
        return _draw_celebration(img)
    return img
