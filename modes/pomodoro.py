#!/usr/bin/env python3
"""
tinyscreen pomodoro — focus timer with state machine and progress visualization.

State machine: WORK (25min) -> BREAK (5min) -> WORK -> ...
Every 4th break is a long break (15min).

Designed for 1920x440 stretched bar LCDs.
"""

import time
import math
from PIL import Image, ImageDraw, ImageFont
from modes.glow import (paste_hero_text, paste_panel, paste_glow_bar,
                         glow_arc, glow_text, glow_circle)

# ── Colors (vivid, saturated — matching sysmon dashboard) ──────────
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

# ── Font cache ──────────────────────────────────────────────────────
_fonts = {}

def font(size):
    if size not in _fonts:
        for path in ['/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
                     '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                     '/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf',
                     '/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf']:
            try:
                _fonts[size] = ImageFont.truetype(path, size)
                return _fonts[size]
            except Exception:
                continue
        _fonts[size] = ImageFont.load_default()
    return _fonts[size]

# ── Timer configuration ─────────────────────────────────────────────
WORK_DURATION    = 25 * 60   # 25 minutes in seconds
BREAK_DURATION   = 5 * 60    # 5 minutes
LONG_BREAK_DURATION = 15 * 60  # 15 minutes
LONG_BREAK_EVERY = 4         # every 4th work session

# ── State ───────────────────────────────────────────────────────────
_phase = 'WORK'         # 'WORK' or 'BREAK'
_phase_duration = WORK_DURATION
_phase_start = 0.0       # monotonic time when phase started
_session_count = 0       # completed work sessions
_total_work_time = 0.0   # total seconds spent in WORK phases
_initialized = False

def init():
    """Initialize / restart the pomodoro timer."""
    global _phase, _phase_duration, _phase_start, _session_count
    global _total_work_time, _initialized
    _phase = 'WORK'
    _phase_duration = WORK_DURATION
    _phase_start = time.monotonic()
    _session_count = 0
    _total_work_time = 0.0
    _initialized = True

def _advance_phase():
    """Transition to the next phase."""
    global _phase, _phase_duration, _phase_start, _session_count, _total_work_time

    if _phase == 'WORK':
        _session_count += 1
        _total_work_time += _phase_duration
        # Determine break type
        if _session_count % LONG_BREAK_EVERY == 0:
            _phase = 'BREAK'
            _phase_duration = LONG_BREAK_DURATION
        else:
            _phase = 'BREAK'
            _phase_duration = BREAK_DURATION
    else:
        # Break -> Work
        _phase = 'WORK'
        _phase_duration = WORK_DURATION

    _phase_start = time.monotonic()

# ── Drawing helpers ─────────────────────────────────────────────────
def _progress_color(pct_remaining):
    """Color based on percentage of time remaining."""
    if pct_remaining > 50:
        return GREEN
    elif pct_remaining > 25:
        return YELLOW
    elif pct_remaining > 10:
        return ORANGE
    else:
        return RED


# ── Utility ─────────────────────────────────────────────────────────
def _lerp_color(c1, c2, t):
    """Linearly interpolate between two RGB tuples."""
    t = max(0.0, min(1.0, t))
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


# ── Cached background and scanlines ────────────────────────────────
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


# ── Panel drawing ──────────────────────────────────────────────────
def _draw_panel(draw, img, x, y, w, h, accent_color=ACCENT):
    paste_panel(draw, img, x, y, w, h, accent_color, _lerp_color)


# ── Glow bar ───────────────────────────────────────────────────────
def _draw_bar(draw, img, x, y, w, h, pct, color):
    """Draw a horizontal progress bar with glow halo and gradient fill."""
    draw.rectangle([x, y, x + w, y + h], fill=(12, 15, 22))

    fill_w = max(0, int(w * min(pct, 100.0) / 100.0))
    if fill_w <= 0:
        return

    from modes.glow import glow_rect
    glow, pad = glow_rect(fill_w, h, color, alpha=100, radius=8)
    if glow:
        img.paste(glow, (x - pad, y - pad), glow)

    # Gradient fill: dim left -> bright right
    for col in range(fill_w):
        t = col / max(fill_w - 1, 1)
        c = _lerp_color(tuple(max(0, v - 80) for v in color), color, t * 0.8 + 0.2)
        draw.line([(x + 1 + col, y + 1), (x + 1 + col, y + h - 1)], fill=c)

    # Bright leading edge
    tip_w = min(4, fill_w)
    bright = tuple(min(255, c + 60) for c in color)
    draw.rectangle([x + fill_w - tip_w, y, x + fill_w, y + h], fill=bright)

    # Top highlight
    hl = tuple(min(255, c + 100) for c in color)
    draw.line([(x + 1, y + 1), (x + fill_w - 1, y + 1)], fill=hl)


# ── Session dots with glow ─────────────────────────────────────────
def _draw_session_dots(img, x, y, count, current_session, dot_size=14, gap=8):
    """Draw session indicator dots. Completed filled, current pulses, future dim."""
    total_dots = max(count + 1, LONG_BREAK_EVERY)

    for i in range(total_dots):
        dx = x + i * (dot_size + gap)
        dy = y

        if i < count:
            # Completed
            color = ACCENT if (i + 1) % LONG_BREAK_EVERY != 0 else CYAN
            glow, _ = glow_circle(dot_size // 2, color, alpha=60, radius=4)
            img.paste(glow, (dx - 8, dy - 8), glow)
            draw_ctx = ImageDraw.Draw(img)
            draw_ctx.ellipse([dx, dy, dx + dot_size, dy + dot_size], fill=color + (255,))
            bright = tuple(min(255, c + 80) for c in color)
            core_pad = 3
            draw_ctx.ellipse([dx + core_pad, dy + core_pad,
                              dx + dot_size - core_pad, dy + dot_size - core_pad],
                             fill=bright + (180,))
        elif i == count:
            # Current — pulsing
            now = time.monotonic()
            pulse = 1.0 + 0.15 * math.sin(now * 3.0)
            r = int(dot_size * pulse / 2)
            inner_color = GREEN if _phase == 'WORK' else CYAN
            glow, _ = glow_circle(r, inner_color, alpha=50, radius=5)
            cx_d = dx + dot_size // 2
            cy_d = dy + dot_size // 2
            img.paste(glow, (cx_d - r - 10, cy_d - r - 10), glow)
            draw_ctx = ImageDraw.Draw(img)
            draw_ctx.ellipse([dx, dy, dx + dot_size, dy + dot_size],
                             outline=ACCENT + (200,), width=2)
            inner_pad = 3
            draw_ctx.ellipse([dx + inner_pad, dy + inner_pad,
                              dx + dot_size - inner_pad, dy + dot_size - inner_pad],
                             fill=inner_color + (220,))
        else:
            # Future
            draw_ctx = ImageDraw.Draw(img)
            draw_ctx.ellipse([dx, dy, dx + dot_size, dy + dot_size],
                             outline=TEXT_DIM + (80,), width=1)


# ── Circular progress with dramatic glow ───────────────────────────
def _draw_circular_progress(draw, img, cx, cy, radius, pct, color, width=12):
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    draw.arc(bbox, 0, 360, fill=(25, 30, 45), width=width)
    if pct > 0:
        start = -90
        end = start + (pct / 100) * 360
        # Cached atmospheric glow
        glow_img, gpad = glow_arc(radius, width, start, end, color,
                                   alpha=80, blur_radius=12, extra_width=16)
        img.paste(glow_img, (cx - radius - gpad, cy - radius - gpad), glow_img)
        # Cached core glow
        glow2, gpad2 = glow_arc(radius, width, start, end, color,
                                 alpha=160, blur_radius=5, extra_width=4)
        img.paste(glow2, (cx - radius - gpad2, cy - radius - gpad2), glow2)
        # Sharp arc
        draw.arc(bbox, start, end, fill=color, width=width)


# ── Main render ─────────────────────────────────────────────────────
def render_frame(w=1920, h=440):
    """Render one frame of the pomodoro timer. Returns PIL Image."""
    global _phase, _phase_duration, _phase_start

    # Auto-initialize if not yet done
    if not _initialized:
        init()

    now = time.monotonic()
    elapsed = now - _phase_start
    remaining = max(0, _phase_duration - elapsed)
    pct_remaining = (remaining / _phase_duration * 100) if _phase_duration > 0 else 0

    # Auto-advance phase when time runs out
    if remaining <= 0:
        _advance_phase()
        elapsed = 0
        remaining = _phase_duration
        pct_remaining = 100.0

    # RGBA workflow — start with cached gradient background
    img = _get_bg(w, h)
    draw = ImageDraw.Draw(img)

    pad = 6
    py0 = pad
    ph = h - pad * 2

    # Color for current state
    progress_color = _progress_color(pct_remaining)
    phase_color = GREEN if _phase == 'WORK' else CYAN

    # ═══════════════════════════════════════════════════════════════
    # Full-width progress bar at top
    # ═══════════════════════════════════════════════════════════════
    bar_h = 18
    _draw_bar(draw, img, pad, py0, w - pad * 2, bar_h, pct_remaining, progress_color)
    draw = ImageDraw.Draw(img)  # re-acquire after paste

    # ═══════════════════════════════════════════════════════════════
    # Main content area
    # ═══════════════════════════════════════════════════════════════
    content_y = py0 + bar_h + pad
    content_h = ph - bar_h - pad

    # ── Left section: Phase + session info (18%) ──
    left_w = int(w * 0.18)
    _draw_panel(draw, img, pad, content_y, left_w, content_h, accent_color=phase_color)
    draw = ImageDraw.Draw(img)

    # Phase label
    phase_label = _phase
    if _phase == 'BREAK':
        if _phase_duration == LONG_BREAK_DURATION:
            phase_label = "LONG BREAK"
        else:
            phase_label = "SHORT BREAK"
    draw.text((pad + 20, content_y + 16), phase_label,
              fill=phase_color, font=font(36))

    # Session number
    sy = content_y + 70
    draw.text((pad + 20, sy), "SESSION", fill=TEXT_DIM, font=font(18))
    draw.text((pad + 20, sy + 26), f"#{_session_count + 1}",
              fill=TEXT_BRIGHT, font=font(52))

    # Completed
    draw.text((pad + 20, sy + 95), "COMPLETED", fill=TEXT_DIM, font=font(18))
    draw.text((pad + 20, sy + 121), str(_session_count),
              fill=ACCENT, font=font(44))

    # Focus time
    total_mins = int(_total_work_time // 60)
    draw.text((pad + 20, sy + 180), "FOCUS TIME", fill=TEXT_DIM, font=font(18))
    if total_mins >= 60:
        draw.text((pad + 20, sy + 206), f"{total_mins // 60}h {total_mins % 60}m",
                  fill=TEXT, font=font(30))
    else:
        draw.text((pad + 20, sy + 206), f"{total_mins}m",
                  fill=TEXT, font=font(30))

    # ── Center section: Massive timer + circular progress (57%) ──
    center_x = pad + left_w + pad
    center_w = int(w * 0.57)
    _draw_panel(draw, img, center_x, content_y, center_w, content_h, accent_color=phase_color)
    draw = ImageDraw.Draw(img)  # re-acquire after paste

    # Circular progress ring (outer)
    ring_cx = center_x + center_w // 2
    ring_cy = content_y + content_h // 2
    ring_radius = min(center_w, content_h) // 2 - 30
    _draw_circular_progress(draw, img, ring_cx, ring_cy, ring_radius,
                            pct_remaining, progress_color, width=12)
    draw = ImageDraw.Draw(img)  # re-acquire after paste

    # Inner ring (thinner, phase color)
    _draw_circular_progress(draw, img, ring_cx, ring_cy, ring_radius - 18,
                            pct_remaining, phase_color, width=4)
    draw = ImageDraw.Draw(img)  # re-acquire after paste

    # Massive countdown timer centered in the ring — with dramatic glow
    mins = int(remaining // 60)
    secs = int(remaining % 60)
    timer_str = f"{mins:02d}:{secs:02d}"

    # Timer + phase label measured together so they center as a unit
    timer_font = font(100)
    phase_font = font(28)

    bbox_t = draw.textbbox((0, 0), timer_str, font=timer_font)
    tw = bbox_t[2] - bbox_t[0]
    th = bbox_t[3] - bbox_t[1]
    bbox_p = draw.textbbox((0, 0), phase_label, font=phase_font)
    pw = bbox_p[2] - bbox_p[0]
    ph_text = bbox_p[3] - bbox_p[1]

    # Total block height: timer + generous gap + phase label
    gap = 20
    total_h = th + gap + ph_text
    # Center the block vertically in the ring
    block_top = ring_cy - total_h // 2

    tx = ring_cx - tw // 2
    ty = block_top
    px = ring_cx - pw // 2
    py = block_top + th + gap

    # Cached timer text glow
    glow_img, gpad, _ = glow_text(timer_str, timer_font, progress_color,
                                   alpha=120, radius=14)
    img.paste(glow_img, (tx - gpad, ty - gpad), glow_img)
    draw = ImageDraw.Draw(img)

    # Sharp timer text
    draw.text((tx, ty), timer_str, fill=progress_color, font=timer_font)

    # Phase label centered below timer
    draw.text((px, py), phase_label, fill=phase_color, font=phase_font)

    # ── Right section: Key info (25%) ──
    right_x = center_x + center_w + pad
    right_w = w - right_x - pad
    _draw_panel(draw, img, right_x, content_y, right_w, content_h, accent_color=phase_color)
    draw = ImageDraw.Draw(img)

    rx = right_x + 20
    ry = content_y + 16

    # Duration
    draw.text((rx, ry), "DURATION", fill=TEXT_DIM, font=font(18))
    draw.text((rx, ry + 24), f"{_phase_duration // 60} minutes",
              fill=TEXT_BRIGHT, font=font(30))

    # Remaining
    ry += 75
    draw.text((rx, ry), "REMAINING", fill=TEXT_DIM, font=font(18))
    draw.text((rx, ry + 24), f"{mins:02d}:{secs:02d}",
              fill=progress_color, font=font(34))

    # Progress
    ry += 80
    done_pct = 100.0 - pct_remaining
    draw.text((rx, ry), "PROGRESS", fill=TEXT_DIM, font=font(18))
    draw.text((rx, ry + 24), f"{done_pct:.0f}%",
              fill=progress_color, font=font(38))

    # Next up
    ry += 85
    draw.text((rx, ry), "NEXT UP", fill=TEXT_DIM, font=font(18))
    if _phase == 'WORK':
        if (_session_count + 1) % LONG_BREAK_EVERY == 0:
            next_label = "LONG BREAK (15m)"
        else:
            next_label = "SHORT BREAK (5m)"
        next_color = CYAN
    else:
        next_label = "WORK (25m)"
        next_color = GREEN
    draw.text((rx, ry + 24), next_label, fill=next_color, font=font(26))

    # ═══════════════════════════════════════════════════════════════
    # Full-width progress bar at bottom
    # ═══════════════════════════════════════════════════════════════
    bot_bar_y = h - pad - bar_h
    _draw_bar(draw, img, pad, bot_bar_y, w - pad * 2, bar_h, pct_remaining, progress_color)
    draw = ImageDraw.Draw(img)

    # ═══════════════════════════════════════════════════════════════
    # Bottom glowing accent line
    # ═══════════════════════════════════════════════════════════════
    for i in range(8):
        a = int(180 * (1.0 - i / 8))
        draw.line([(0, h - 1 - i), (w, h - 1 - i)], fill=ACCENT + (a,))

    # Apply scanlines and convert RGBA -> RGB
    img = Image.alpha_composite(img, _get_scanlines(w, h))
    out = Image.new('RGB', (w, h), BG)
    out.paste(img, (0, 0), img)
    return out


# ── Standalone mode ─────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    if '--once' in sys.argv:
        init()
        # Simulate some time passing for a more interesting screenshot
        time.sleep(0.1)
        img = render_frame()
        img.save('/tmp/pomodoro.png')
        print("Saved to /tmp/pomodoro.png")
    elif '--demo' in sys.argv:
        # Quick demo: render multiple frames showing different states
        def _demo_render(offset):
            global _phase_start
            _phase_start = time.monotonic() - offset
            return render_frame()
        init()
        for i, offset in enumerate([0, 300, 750, 1200, 1500]):
            img = _demo_render(offset)
            img.save(f'/tmp/pomodoro_{i}.png')
            print(f"Saved /tmp/pomodoro_{i}.png (elapsed={offset}s)")
    else:
        print("Usage:")
        print("  python3 pomodoro.py --once   # save one frame to /tmp/pomodoro.png")
        print("  python3 pomodoro.py --demo   # save 5 frames at different times")
        print("  Use via tinyscreen daemon for live display")
