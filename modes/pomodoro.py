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

# ── Colors ──────────────────────────────────────────────────────────
BG         = (10, 12, 18)
PANEL_BG   = (18, 22, 32)
BORDER     = (35, 42, 58)
ACCENT     = (0, 170, 255)
TEXT       = (200, 210, 225)
TEXT_DIM   = (100, 110, 130)
GREEN      = (0, 220, 100)
RED        = (255, 60, 60)
YELLOW     = (255, 200, 0)
ORANGE     = (255, 140, 0)
CYAN       = (0, 220, 220)

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

def _draw_bar(draw, x, y, w, h, pct, color, bg=PANEL_BG):
    """Draw a horizontal progress bar with gradient effect."""
    draw.rectangle([x, y, x + w, y + h], fill=bg, outline=BORDER)
    fill_w = max(0, int(w * min(pct, 100) / 100))
    if fill_w > 0:
        dim = tuple(max(0, c - 60) for c in color)
        draw.rectangle([x + 1, y + 1, x + fill_w, y + h - 1], fill=dim)
        # Bright tip
        tip_w = min(4, fill_w)
        draw.rectangle([x + fill_w - tip_w, y + 1, x + fill_w, y + h - 1], fill=color)

def _draw_session_dots(draw, x, y, count, current_session, dot_size=14, gap=8):
    """Draw session indicator dots. Filled for completed, outlined for pending."""
    total_dots = max(count + 1, LONG_BREAK_EVERY)  # show at least one cycle
    for i in range(total_dots):
        dx = x + i * (dot_size + gap)
        dy = y
        if i < count:
            # Completed — filled
            color = ACCENT
            if (i + 1) % LONG_BREAK_EVERY == 0:
                color = CYAN  # long break marker
            draw.ellipse([dx, dy, dx + dot_size, dy + dot_size], fill=color)
        elif i == count:
            # Current — outlined with pulse
            draw.ellipse([dx, dy, dx + dot_size, dy + dot_size], outline=ACCENT, width=2)
            # Inner fill based on phase
            inner_color = GREEN if _phase == 'WORK' else CYAN
            inner_pad = 3
            draw.ellipse([dx + inner_pad, dy + inner_pad,
                          dx + dot_size - inner_pad, dy + dot_size - inner_pad],
                         fill=inner_color)
        else:
            # Future — dim outline
            draw.ellipse([dx, dy, dx + dot_size, dy + dot_size],
                         outline=BORDER, width=1)

def _draw_circular_progress(draw, cx, cy, radius, pct, color, width=8):
    """Draw a circular progress arc."""
    # Background circle
    draw.arc([cx - radius, cy - radius, cx + radius, cy + radius],
             0, 360, fill=BORDER, width=width)
    # Progress arc (starts at top, goes clockwise)
    if pct > 0:
        start_angle = -90
        end_angle = start_angle + (pct / 100) * 360
        draw.arc([cx - radius, cy - radius, cx + radius, cy + radius],
                 start_angle, end_angle, fill=color, width=width)

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

    img = Image.new('RGB', (w, h), BG)
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
    _draw_bar(draw, pad, py0, w - pad * 2, bar_h, pct_remaining, progress_color)

    # ═══════════════════════════════════════════════════════════════
    # Main content area
    # ═══════════════════════════════════════════════════════════════
    content_y = py0 + bar_h + pad
    content_h = ph - bar_h - pad

    # ── Left section: Phase info + session count (20%) ──
    left_w = int(w * 0.20)
    draw.rectangle([pad, content_y, pad + left_w, content_y + content_h],
                   fill=PANEL_BG, outline=BORDER)
    draw.rectangle([pad, content_y, pad + left_w, content_y + 2], fill=phase_color)

    # Phase label
    phase_label = _phase
    if _phase == 'BREAK':
        if _phase_duration == LONG_BREAK_DURATION:
            phase_label = "LONG BREAK"
        else:
            phase_label = "SHORT BREAK"

    draw.text((pad + 16, content_y + 14), phase_label,
              fill=phase_color, font=font(38))

    # Session info
    sy = content_y + 70
    draw.text((pad + 16, sy), "SESSION", fill=TEXT_DIM, font=font(14))
    draw.text((pad + 16, sy + 22), f"#{_session_count + 1}",
              fill=TEXT, font=font(48))

    # Completed sessions
    draw.text((pad + 16, sy + 80), "COMPLETED", fill=TEXT_DIM, font=font(14))
    draw.text((pad + 16, sy + 100), str(_session_count),
              fill=ACCENT, font=font(42))

    # Total focus time
    total_mins = int(_total_work_time // 60)
    draw.text((pad + 16, sy + 155), "FOCUS TIME", fill=TEXT_DIM, font=font(14))
    if total_mins >= 60:
        draw.text((pad + 16, sy + 175), f"{total_mins // 60}h {total_mins % 60}m",
                  fill=TEXT, font=font(28))
    else:
        draw.text((pad + 16, sy + 175), f"{total_mins}m",
                  fill=TEXT, font=font(28))

    # Session dots at bottom of left panel
    dots_y = content_y + content_h - 30
    _draw_session_dots(draw, pad + 16, dots_y, _session_count, _session_count)

    # ── Center section: Massive timer + circular progress (55%) ──
    center_x = pad + left_w + pad
    center_w = int(w * 0.55)
    draw.rectangle([center_x, content_y, center_x + center_w, content_y + content_h],
                   fill=PANEL_BG, outline=BORDER)
    draw.rectangle([center_x, content_y, center_x + center_w, content_y + 2],
                   fill=phase_color)

    # Circular progress ring
    ring_cx = center_x + center_w // 2
    ring_cy = content_y + content_h // 2
    ring_radius = min(center_w, content_h) // 2 - 30
    _draw_circular_progress(draw, ring_cx, ring_cy, ring_radius,
                            pct_remaining, progress_color, width=12)

    # Inner ring (thinner, phase color)
    _draw_circular_progress(draw, ring_cx, ring_cy, ring_radius - 18,
                            pct_remaining, phase_color, width=4)

    # Massive countdown timer centered in the ring
    mins = int(remaining // 60)
    secs = int(remaining % 60)
    timer_str = f"{mins:02d}:{secs:02d}"

    timer_font = font(130)
    bbox = draw.textbbox((0, 0), timer_str, font=timer_font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = ring_cx - tw // 2
    ty = ring_cy - th // 2 - 20

    # Glow effect
    glow = tuple(max(0, c // 3) for c in progress_color)
    draw.text((tx - 1, ty - 1), timer_str, fill=glow, font=timer_font)
    draw.text((tx + 1, ty + 1), timer_str, fill=glow, font=timer_font)
    draw.text((tx, ty), timer_str, fill=progress_color, font=timer_font)

    # Phase label under timer
    phase_font = font(24)
    bbox_p = draw.textbbox((0, 0), phase_label, font=phase_font)
    pw = bbox_p[2] - bbox_p[0]
    px = ring_cx - pw // 2
    py = ty + th + 20
    draw.text((px, py), phase_label, fill=phase_color, font=phase_font)

    # ── Right section: Detailed info (25%) ──
    right_x = center_x + center_w + pad
    right_w = w - right_x - pad
    draw.rectangle([right_x, content_y, right_x + right_w, content_y + content_h],
                   fill=PANEL_BG, outline=BORDER)
    draw.rectangle([right_x, content_y, right_x + right_w, content_y + 2],
                   fill=phase_color)

    ry = content_y + 14
    row_gap = 44

    # Duration info
    draw.text((right_x + 16, ry), "DURATION", fill=TEXT_DIM, font=font(14))
    draw.text((right_x + 16, ry + 20), f"{_phase_duration // 60} minutes",
              fill=TEXT, font=font(26))

    # Elapsed
    ry += row_gap + 20
    elapsed_mins = int(elapsed // 60)
    elapsed_secs = int(elapsed % 60)
    draw.text((right_x + 16, ry), "ELAPSED", fill=TEXT_DIM, font=font(14))
    draw.text((right_x + 16, ry + 20), f"{elapsed_mins:02d}:{elapsed_secs:02d}",
              fill=TEXT, font=font(26))

    # Remaining
    ry += row_gap + 20
    draw.text((right_x + 16, ry), "REMAINING", fill=TEXT_DIM, font=font(14))
    draw.text((right_x + 16, ry + 20), f"{mins:02d}:{secs:02d}",
              fill=progress_color, font=font(26))

    # Percentage
    ry += row_gap + 20
    draw.text((right_x + 16, ry), "PROGRESS", fill=TEXT_DIM, font=font(14))
    done_pct = 100.0 - pct_remaining
    draw.text((right_x + 16, ry + 20), f"{done_pct:.0f}%",
              fill=progress_color, font=font(34))

    # Next phase preview
    ry += row_gap + 30
    draw.rectangle([right_x + 1, ry, right_x + right_w - 1, ry + 1], fill=BORDER)
    ry += 8
    draw.text((right_x + 16, ry), "NEXT UP", fill=TEXT_DIM, font=font(14))
    if _phase == 'WORK':
        if (_session_count + 1) % LONG_BREAK_EVERY == 0:
            next_label = "LONG BREAK (15m)"
            next_color = CYAN
        else:
            next_label = "SHORT BREAK (5m)"
            next_color = CYAN
    else:
        next_label = "WORK (25m)"
        next_color = GREEN
    draw.text((right_x + 16, ry + 22), next_label,
              fill=next_color, font=font(22))

    # Cycle progress (how many sessions until long break)
    ry += 60
    cycle_pos = _session_count % LONG_BREAK_EVERY
    if _phase == 'WORK':
        cycle_pos_display = cycle_pos + 1
    else:
        cycle_pos_display = cycle_pos
    draw.text((right_x + 16, ry), "CYCLE", fill=TEXT_DIM, font=font(14))
    draw.text((right_x + 16, ry + 20),
              f"{cycle_pos_display} / {LONG_BREAK_EVERY}",
              fill=TEXT, font=font(22))

    # ═══════════════════════════════════════════════════════════════
    # Full-width progress bar at bottom (duplicate for visibility)
    # ═══════════════════════════════════════════════════════════════
    bot_bar_y = h - pad - bar_h
    _draw_bar(draw, pad, bot_bar_y, w - pad * 2, bar_h, pct_remaining, progress_color)

    # Bottom accent line
    draw.rectangle([0, h - 2, w, h], fill=(0, 80, 140))

    return img


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
