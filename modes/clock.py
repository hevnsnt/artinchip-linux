#!/usr/bin/env python3
"""
tinyscreen clock — premium clock + weather + system info dashboard.

Left 40%:  massive digital clock with date, timezone
Middle 30%: atmospheric weather scene (the hero panel)
Right 30%:  system quick stats with bars

Designed for 1920x440 stretched bar LCDs, rendered at ~15fps with PIL/Pillow.
Weather scenes use layered RGBA compositing for soft glows, depth, and atmosphere.
"""

import math
import os
import random
import subprocess
import time

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Font cache
# ---------------------------------------------------------------------------
_fonts: dict = {}

_FONT_PATHS = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf',
]

# Resolved at first call
_font_regular: str | None = None
_font_bold: str | None = None


def _resolve_fonts():
    global _font_regular, _font_bold
    if _font_regular is not None:
        return
    for p in _FONT_PATHS:
        if os.path.isfile(p):
            if _font_regular is None and 'Bold' not in p:
                _font_regular = p
            if _font_bold is None and 'Bold' in p:
                _font_bold = p
    if _font_regular is None:
        _font_regular = _font_bold or ''
    if _font_bold is None:
        _font_bold = _font_regular


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    _resolve_fonts()
    key = (size, bold)
    if key not in _fonts:
        path = _font_bold if bold else _font_regular
        try:
            _fonts[key] = ImageFont.truetype(path, size)
        except Exception:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]


# ---------------------------------------------------------------------------
# Color palettes per weather condition
# ---------------------------------------------------------------------------
PALETTES = {
    'overcast': {
        'grad_top': (18, 22, 35), 'grad_bot': (30, 35, 48),
        'cloud_far': (65, 70, 90), 'cloud_mid': (80, 85, 105),
        'cloud_near': (95, 100, 125),
    },
    'rain': {
        'grad_top': (12, 15, 30), 'grad_bot': (20, 25, 40),
        'cloud': (28, 32, 48), 'drop_far': (60, 100, 180),
        'drop_near': (100, 150, 255), 'mist': (30, 45, 70),
    },
    'thunder': {
        'grad_top': (10, 8, 20), 'grad_bot': (20, 15, 35),
        'cloud': (22, 18, 38), 'flash': (200, 200, 255),
        'bolt': (220, 220, 255), 'drop': (80, 110, 200),
    },
    'snow': {
        'grad_top': (25, 30, 50), 'grad_bot': (45, 50, 70),
        'cloud': (55, 60, 78), 'flake': (220, 230, 255),
    },
    'sunny': {
        'grad_top': (20, 40, 120), 'grad_bot': (220, 160, 50),
        'sun_core': (255, 245, 200), 'sun_glow': (255, 220, 100),
        'sun_outer': (255, 180, 60), 'grass': (30, 160, 50),
    },
    'hot': {
        'grad_top': (60, 10, 5), 'grad_bot': (180, 40, 10),
        'sun_core': (255, 80, 10), 'sun_glow': (255, 50, 0),
        'shimmer': (255, 120, 30),
    },
    'fog': {
        'grad_top': (20, 22, 30), 'grad_bot': (35, 38, 48),
        'band_light': (65, 68, 78), 'band_dark': (45, 48, 58),
    },
    'partly_cloudy': {
        'grad_top': (25, 45, 100), 'grad_bot': (60, 80, 140),
        'sun_core': (255, 240, 180), 'sun_glow': (255, 210, 100),
        'cloud': (140, 150, 170),
    },
}

# UI colors
BG = (10, 12, 18)
PANEL_BG = (16, 20, 30)
BORDER = (35, 42, 58)
ACCENT = (0, 200, 255)       # cyan time color
ACCENT_DIM = (0, 80, 120)
TEXT = (200, 210, 225)
TEXT_DIM = (100, 110, 130)
TEXT_BRIGHT = (240, 245, 255)
GREEN = (0, 220, 100)
RED = (255, 60, 60)
YELLOW = (255, 200, 0)
ORANGE = (255, 140, 0)
CYAN = (0, 220, 220)

# ---------------------------------------------------------------------------
# Weather state
# ---------------------------------------------------------------------------
_weather: dict | None = None
_weather_last_fetch: float = 0.0
_weather_error: str | None = None
WEATHER_INTERVAL = 300

# ---------------------------------------------------------------------------
# Animation state  (all module-level for persistence across frames)
# ---------------------------------------------------------------------------
_anim_frame: int = 0

# Cloud layers: list of dicts with x_offset, y, scale, speed, alpha
_clouds: list[dict] = []
_clouds_inited: bool = False

# Rain drops: (x, y, speed, length, brightness)
_rain_drops: list = []

# Snow flakes: (x, y, speed_y, drift_x, size, phase)
_snow_flakes: list = []

# Fog bands: (x_offset, y, width, height, speed, alpha)
_fog_bands: list = []
_fog_inited: bool = False

# Lightning
_lightning_cooldown: int = 0
_lightning_active: int = 0      # frames remaining of flash
_lightning_bolt: list = []      # bolt points

# Sun rays rotation angle
_sun_ray_angle: float = 0.0

# Grass blades for sunny: (x, height, phase)
_grass_blades: list = []
_grass_inited: bool = False

# Hot sun pulse
_hot_pulse_phase: float = 0.0

# Splash particles for rain
_splashes: list = []

# ---------------------------------------------------------------------------
# System info state
# ---------------------------------------------------------------------------
_cpu_smooth: float = 0.0
_cached_ip: str = '--'
_cached_ip_time: float = 0.0
_cached_hostname: str | None = None
_cached_kernel: str | None = None

# ---------------------------------------------------------------------------
# Helper: vertical gradient on an RGBA image
# ---------------------------------------------------------------------------

def _gradient_fill(img: Image.Image, top_color: tuple, bot_color: tuple):
    """Fill an RGBA image with a smooth vertical gradient."""
    w, h = img.size
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(top_color[0] + (bot_color[0] - top_color[0]) * t)
        g = int(top_color[1] + (bot_color[1] - top_color[1]) * t)
        b = int(top_color[2] + (bot_color[2] - top_color[2]) * t)
        draw.line([(0, y), (w - 1, y)], fill=(r, g, b, 255))


def _lerp_color(c1: tuple, c2: tuple, t: float) -> tuple:
    """Linearly interpolate between two RGB(A) tuples."""
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


# ---------------------------------------------------------------------------
# Helper: draw soft glow (concentric ellipses)
# ---------------------------------------------------------------------------

def _draw_glow(draw: ImageDraw.Draw, cx: int, cy: int, radius: int,
               color: tuple, steps: int = 12, intensity: float = 1.0):
    """Draw a soft glow centered at (cx, cy) using layered semi-transparent ellipses.

    intensity controls overall brightness (1.0 = normal, 2.0 = double).
    """
    for i in range(steps, 0, -1):
        t = i / steps
        r = int(radius * t)
        # Quadratic falloff: brighter in center, soft fade at edges
        # Base alpha up to 90 at center (was 30 — far too dim on dark backgrounds)
        alpha = int(90 * intensity * (1.0 - t) ** 0.7)
        alpha = min(255, max(0, alpha))
        c = (color[0], color[1], color[2], alpha)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)


def _draw_soft_ellipse(draw: ImageDraw.Draw, cx: int, cy: int,
                       rx: int, ry: int, color: tuple, alpha: int,
                       steps: int = 6):
    """Draw a soft-edged ellipse by layering from outer (dimmer) to inner (brighter)."""
    for i in range(steps):
        t = i / max(steps - 1, 1)
        crx = int(rx * (1.0 - t * 0.3))
        cry = int(ry * (1.0 - t * 0.3))
        a = int(alpha * (0.3 + 0.7 * t))
        c = (color[0], color[1], color[2], min(255, a))
        draw.ellipse([cx - crx, cy - cry, cx + crx, cy + cry], fill=c)


# ---------------------------------------------------------------------------
# Cloud drawing  (puffy, multi-lobe, with soft edges)
# ---------------------------------------------------------------------------

def _draw_cloud(draw: ImageDraw.Draw, x: float, y: float, w: float, h: float,
                color: tuple, alpha: int = 180):
    """Draw a soft puffy cloud from overlapping ellipses."""
    base_a = alpha
    # Main body — wide ellipse
    _draw_soft_ellipse(draw, int(x + w * 0.5), int(y + h * 0.55),
                       int(w * 0.48), int(h * 0.38), color, base_a, 4)
    # Left lobe
    _draw_soft_ellipse(draw, int(x + w * 0.25), int(y + h * 0.5),
                       int(w * 0.28), int(h * 0.35), color, base_a, 4)
    # Right lobe
    _draw_soft_ellipse(draw, int(x + w * 0.72), int(y + h * 0.5),
                       int(w * 0.3), int(h * 0.32), color, base_a, 4)
    # Top puff
    _draw_soft_ellipse(draw, int(x + w * 0.45), int(y + h * 0.28),
                       int(w * 0.25), int(h * 0.3), color, int(base_a * 0.9), 4)
    # Secondary top puff
    _draw_soft_ellipse(draw, int(x + w * 0.62), int(y + h * 0.32),
                       int(w * 0.2), int(h * 0.25), color, int(base_a * 0.85), 3)


# ---------------------------------------------------------------------------
# Init cloud layers for a given panel size
# ---------------------------------------------------------------------------

def _init_clouds(pw: int, ph: int, condition: str):
    global _clouds, _clouds_inited
    _clouds = []
    cond = condition.lower()

    if 'thunder' in cond or 'storm' in cond:
        # Heavy dark cloud ceiling
        configs = [
            {'y_frac': 0.0, 'scale': 1.8, 'speed': 0.08, 'alpha': 200, 'color_key': 'far'},
            {'y_frac': -0.05, 'scale': 2.2, 'speed': 0.12, 'alpha': 220, 'color_key': 'mid'},
            {'y_frac': 0.02, 'scale': 1.5, 'speed': 0.06, 'alpha': 180, 'color_key': 'far'},
            {'y_frac': -0.02, 'scale': 2.0, 'speed': 0.15, 'alpha': 240, 'color_key': 'near'},
        ]
    elif 'snow' in cond:
        configs = [
            {'y_frac': 0.02, 'scale': 1.3, 'speed': 0.05, 'alpha': 140, 'color_key': 'far'},
            {'y_frac': -0.02, 'scale': 1.6, 'speed': 0.08, 'alpha': 160, 'color_key': 'mid'},
            {'y_frac': 0.05, 'scale': 1.0, 'speed': 0.04, 'alpha': 120, 'color_key': 'far'},
        ]
    elif 'rain' in cond or 'drizzle' in cond:
        configs = [
            {'y_frac': 0.0, 'scale': 1.5, 'speed': 0.07, 'alpha': 170, 'color_key': 'far'},
            {'y_frac': -0.03, 'scale': 2.0, 'speed': 0.1, 'alpha': 200, 'color_key': 'mid'},
            {'y_frac': 0.01, 'scale': 1.3, 'speed': 0.05, 'alpha': 150, 'color_key': 'far'},
            {'y_frac': -0.01, 'scale': 1.8, 'speed': 0.13, 'alpha': 210, 'color_key': 'near'},
        ]
    elif 'overcast' in cond or 'cloudy' in cond:
        configs = [
            {'y_frac': 0.05, 'scale': 1.4, 'speed': 0.03, 'alpha': 200, 'color_key': 'far'},
            {'y_frac': 0.18, 'scale': 1.8, 'speed': 0.05, 'alpha': 220, 'color_key': 'mid'},
            {'y_frac': 0.30, 'scale': 1.2, 'speed': 0.02, 'alpha': 190, 'color_key': 'far'},
            {'y_frac': 0.10, 'scale': 2.0, 'speed': 0.07, 'alpha': 230, 'color_key': 'near'},
            {'y_frac': 0.40, 'scale': 1.5, 'speed': 0.04, 'alpha': 200, 'color_key': 'mid'},
        ]
    elif 'partly' in cond:
        configs = [
            {'y_frac': 0.15, 'scale': 1.2, 'speed': 0.04, 'alpha': 160, 'color_key': 'far'},
            {'y_frac': 0.3, 'scale': 1.0, 'speed': 0.06, 'alpha': 170, 'color_key': 'mid'},
            {'y_frac': 0.05, 'scale': 1.4, 'speed': 0.05, 'alpha': 150, 'color_key': 'far'},
        ]
    else:
        configs = []

    for i, cfg in enumerate(configs):
        _clouds.append({
            'x': random.uniform(-0.3, 1.0) * pw,
            'y_frac': cfg['y_frac'],
            'scale': cfg['scale'],
            'speed': cfg['speed'],
            'alpha': cfg['alpha'],
            'color_key': cfg['color_key'],
            'phase': random.uniform(0, math.tau),
        })
    _clouds_inited = True


# ---------------------------------------------------------------------------
# Init fog bands
# ---------------------------------------------------------------------------

def _init_fog(pw: int, ph: int):
    global _fog_bands, _fog_inited
    _fog_bands = []
    # Large soft fog puffs at different depths
    for i in range(20):
        _fog_bands.append({
            'x': random.uniform(-0.2, 1.2) * pw,
            'y': random.uniform(0.1, 0.95) * ph,
            'rx': random.uniform(120, 350),   # horizontal radius
            'ry': random.uniform(40, 100),     # vertical radius (wide and flat)
            'speed': random.uniform(0.15, 0.6) * random.choice([-1, 1]),
            'opacity': random.uniform(0.04, 0.12),
            'phase': random.uniform(0, math.tau),
            'depth': random.uniform(0.3, 1.0),  # 0=far, 1=near
        })
    # Sort by depth so far puffs draw first
    _fog_bands.sort(key=lambda b: b['depth'])
    _fog_inited = True


# ---------------------------------------------------------------------------
# Init rain
# ---------------------------------------------------------------------------

def _init_rain(pw: int, ph: int, count: int = 60):
    global _rain_drops, _splashes
    _rain_drops = []
    _splashes = []
    for _ in range(count):
        _rain_drops.append({
            'x': random.uniform(0, pw),
            'y': random.uniform(-20, ph),
            'speed': random.uniform(8, 18),
            'length': random.uniform(12, 30),
            'bright': random.uniform(0.3, 1.0),
            'wind': random.uniform(1.5, 3.5),
        })


# ---------------------------------------------------------------------------
# Init snow
# ---------------------------------------------------------------------------

def _init_snow(pw: int, ph: int, count: int = 70):
    global _snow_flakes
    _snow_flakes = []
    for _ in range(count):
        _snow_flakes.append({
            'x': random.uniform(0, pw),
            'y': random.uniform(-20, ph),
            'speed': random.uniform(0.5, 2.5),
            'drift': random.uniform(-1.0, 1.0),
            'size': random.uniform(1.5, 4.5),
            'phase': random.uniform(0, math.tau),
            'depth': random.uniform(0.3, 1.0),     # 0=far, 1=near
        })


# ---------------------------------------------------------------------------
# Init grass for sunny
# ---------------------------------------------------------------------------

def _init_grass(pw: int):
    global _grass_blades, _grass_inited
    _grass_blades = []
    for x in range(3, pw - 3, 5):
        _grass_blades.append({
            'x': x,
            'height': random.uniform(8, 22),
            'phase': random.uniform(0, math.tau),
            'shade': random.uniform(0.7, 1.0),
        })
    _grass_inited = True


# ---------------------------------------------------------------------------
# Weather scene rendering  (the hero panel)
# ---------------------------------------------------------------------------

def _classify_condition(condition: str, temp_f) -> str:
    """Map wttr.in condition string to our scene type."""
    cond = condition.lower()
    is_hot = False
    try:
        if temp_f and float(temp_f) >= 95:
            is_hot = True
    except (ValueError, TypeError):
        pass

    if is_hot:
        return 'hot'
    if 'thunder' in cond or 'storm' in cond:
        return 'thunder'
    if 'snow' in cond or 'blizzard' in cond or 'sleet' in cond or 'ice' in cond:
        return 'snow'
    if 'rain' in cond or 'drizzle' in cond or 'shower' in cond:
        return 'rain'
    if 'fog' in cond or 'mist' in cond or 'haze' in cond:
        return 'fog'
    if 'overcast' in cond:
        return 'overcast'
    if 'cloudy' in cond and 'partly' not in cond:
        return 'overcast'
    if 'partly' in cond or 'cloud' in cond:
        return 'partly_cloudy'
    if 'sun' in cond or 'clear' in cond:
        return 'sunny'
    # Default: partly cloudy
    return 'partly_cloudy'


def _render_weather_scene(pw: int, ph: int, condition: str, temp_f) -> Image.Image:
    """Render the full weather scene as an RGBA image to be composited."""
    global _anim_frame, _clouds_inited, _fog_inited, _grass_inited
    global _rain_drops, _snow_flakes, _splashes
    global _lightning_cooldown, _lightning_active, _lightning_bolt
    global _sun_ray_angle, _hot_pulse_phase

    _anim_frame += 1
    t = _anim_frame * 0.067  # ~15fps, so this is roughly seconds

    scene_type = _classify_condition(condition, temp_f)
    pal = PALETTES.get(scene_type, PALETTES['partly_cloudy'])

    # Create RGBA canvas
    scene = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))

    # ── Background gradient ──────────────────────────────────────
    grad_top = pal.get('grad_top', (20, 25, 40))
    grad_bot = pal.get('grad_bot', (40, 45, 60))
    _gradient_fill(scene, grad_top, grad_bot)

    draw = ImageDraw.Draw(scene)

    # ── SCENE-SPECIFIC RENDERING ────────────────────────────────
    if scene_type == 'sunny':
        _render_sunny(scene, draw, pw, ph, t, pal)
    elif scene_type == 'hot':
        _render_hot(scene, draw, pw, ph, t, pal)
    elif scene_type == 'overcast':
        _render_overcast(scene, draw, pw, ph, t, pal, condition)
    elif scene_type == 'rain':
        _render_rain(scene, draw, pw, ph, t, pal, condition)
    elif scene_type == 'thunder':
        _render_thunder(scene, draw, pw, ph, t, pal)
    elif scene_type == 'snow':
        _render_snow(scene, draw, pw, ph, t, pal)
    elif scene_type == 'fog':
        _render_fog(scene, draw, pw, ph, t, pal)
    elif scene_type == 'partly_cloudy':
        _render_partly_cloudy(scene, draw, pw, ph, t, pal)

    return scene


# ── Sunny ────────────────────────────────────────────────────────────

def _render_sunny(scene: Image.Image, draw: ImageDraw.Draw,
                  pw: int, ph: int, t: float, pal: dict):
    global _sun_ray_angle, _grass_inited

    # Sun position — upper right area
    sun_cx = int(pw * 0.72)
    sun_cy = int(ph * 0.30)
    sun_r = int(min(pw, ph) * 0.14)

    # Outer glow (huge, very soft) — high intensity for visible warmth
    _draw_glow(draw, sun_cx, sun_cy, sun_r * 5,
               pal['sun_outer'], steps=20, intensity=2.5)
    _draw_glow(draw, sun_cx, sun_cy, sun_r * 3,
               pal['sun_glow'], steps=16, intensity=2.0)

    # Animated rays
    _sun_ray_angle = t * 0.15
    ray_layer = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    ray_draw = ImageDraw.Draw(ray_layer)
    for i in range(12):
        angle = _sun_ray_angle + i * math.tau / 12
        pulse = 0.8 + 0.2 * math.sin(t * 1.5 + i * 0.7)
        r_inner = sun_r + 10
        r_outer = int((sun_r * 2.5 + 30) * pulse)
        half_angle = math.tau / 48  # thin wedge
        # Draw each ray as a thin triangle
        x0 = sun_cx + int(math.cos(angle) * r_inner)
        y0 = sun_cy + int(math.sin(angle) * r_inner)
        x1 = sun_cx + int(math.cos(angle - half_angle) * r_outer)
        y1 = sun_cy + int(math.sin(angle - half_angle) * r_outer)
        x2 = sun_cx + int(math.cos(angle + half_angle) * r_outer)
        y2 = sun_cy + int(math.sin(angle + half_angle) * r_outer)
        a = int(50 * pulse)
        ray_draw.polygon([(x0, y0), (x1, y1), (x2, y2)],
                         fill=(255, 230, 120, a))
    scene.alpha_composite(ray_layer)

    # Sun core — bright white-yellow center
    draw = ImageDraw.Draw(scene)
    _draw_glow(draw, sun_cx, sun_cy, sun_r + 20, pal['sun_glow'], steps=10, intensity=2.5)
    # Solid core
    draw.ellipse([sun_cx - sun_r, sun_cy - sun_r,
                  sun_cx + sun_r, sun_cy + sun_r],
                 fill=(255, 250, 220, 255))
    # Bright center
    cr = sun_r // 2
    draw.ellipse([sun_cx - cr, sun_cy - cr, sun_cx + cr, sun_cy + cr],
                 fill=(255, 255, 245, 255))

    # Warm horizon glow at bottom
    horizon = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    h_draw = ImageDraw.Draw(horizon)
    for y in range(ph - 60, ph):
        frac = (y - (ph - 60)) / 60.0
        a = int(80 * frac)
        h_draw.line([(0, y), (pw - 1, y)], fill=(255, 190, 60, a))
    scene.alpha_composite(horizon)

    # Grass at bottom
    if not _grass_inited:
        _init_grass(pw)
    draw = ImageDraw.Draw(scene)
    ground_y = ph - 6
    # Ground strip
    draw.rectangle([0, ground_y, pw, ph], fill=(20, 80, 30, 200))
    gc = pal['grass']
    for blade in _grass_blades:
        sway = math.sin(t * 2.0 + blade['phase']) * 5
        shade = blade['shade']
        bc = (int(gc[0] * shade), int(gc[1] * shade), int(gc[2] * shade), 220)
        bx = blade['x']
        bh = blade['height']
        draw.line([(bx, ground_y), (int(bx + sway), int(ground_y - bh))],
                  fill=bc, width=2)


# ── Hot ──────────────────────────────────────────────────────────────

def _render_hot(scene: Image.Image, draw: ImageDraw.Draw,
                pw: int, ph: int, t: float, pal: dict):
    global _hot_pulse_phase
    _hot_pulse_phase = t

    # Pulsing red tint overlay — throbs visibly
    pulse = 0.5 + 0.5 * math.sin(t * 2.5)
    tint = Image.new('RGBA', (pw, ph), (200, 20, 0, int(40 * pulse)))
    scene.alpha_composite(tint)

    draw = ImageDraw.Draw(scene)

    # Heat shimmer — wavy distortion lines rising from bottom
    shimmer_layer = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shimmer_layer)
    for i in range(20):
        base_y = ph - 10 - i * (ph // 20)
        alpha_line = max(10, 60 - i * 3)
        points = []
        for x in range(0, pw, 3):
            wave = math.sin(t * 3.0 + x * 0.015 + i * 0.8) * (4 + i * 0.3)
            points.append((x, int(base_y + wave)))
        if len(points) > 1:
            s_draw.line(points, fill=(255, 150, 50, alpha_line), width=1)
    scene.alpha_composite(shimmer_layer)

    # ANGRY SUN
    draw = ImageDraw.Draw(scene)
    sun_r = int(min(pw, ph) * 0.22)
    sun_cx = int(pw * 0.65)
    sun_cy = int(ph * 0.35)
    pulse_r = int(math.sin(t * 3.0) * 6)

    # Outer pulsing glow — angry red
    _draw_glow(draw, sun_cx, sun_cy, sun_r * 3 + pulse_r,
               (255, 40, 0), steps=18, intensity=2.0)
    _draw_glow(draw, sun_cx, sun_cy, sun_r * 2 + pulse_r,
               (255, 80, 10), steps=14, intensity=2.5)

    # Sun body
    draw.ellipse([sun_cx - sun_r - pulse_r, sun_cy - sun_r - pulse_r,
                  sun_cx + sun_r + pulse_r, sun_cy + sun_r + pulse_r],
                 fill=(220, 50, 0, 255))
    # Brighter center
    inner_r = int(sun_r * 0.7)
    draw.ellipse([sun_cx - inner_r, sun_cy - inner_r,
                  sun_cx + inner_r, sun_cy + inner_r],
                 fill=(255, 80, 10, 255))

    # Angry furrowed eyebrows
    brow_w = sun_r // 2
    brow_y = sun_cy - sun_r // 4
    # Left brow (angled down toward center)
    draw.line([(sun_cx - brow_w - 5, brow_y - 12),
               (sun_cx - brow_w // 3, brow_y + 4)],
              fill=(120, 0, 0, 255), width=5)
    # Right brow
    draw.line([(sun_cx + brow_w + 5, brow_y - 12),
               (sun_cx + brow_w // 3, brow_y + 4)],
              fill=(120, 0, 0, 255), width=5)

    # Angry squinting eyes
    eye_y = sun_cy - sun_r // 8
    eye_w = sun_r // 5
    eye_h = sun_r // 8
    # Left eye — narrow squint
    draw.ellipse([sun_cx - sun_r // 3 - eye_w, eye_y - eye_h,
                  sun_cx - sun_r // 3 + eye_w, eye_y + eye_h],
                 fill=(100, 0, 0, 255))
    # Right eye
    draw.ellipse([sun_cx + sun_r // 3 - eye_w, eye_y - eye_h,
                  sun_cx + sun_r // 3 + eye_w, eye_y + eye_h],
                 fill=(100, 0, 0, 255))

    # Jagged frown
    mouth_y = sun_cy + sun_r // 3
    mouth_hw = sun_r // 2
    mouth_pts = []
    for mx in range(-mouth_hw, mouth_hw + 1, 6):
        jag = math.sin(mx * 0.5 + t * 5) * 4
        my = mouth_y + abs(mx) * 0.15 + jag
        mouth_pts.append((sun_cx + mx, int(my)))
    if len(mouth_pts) > 1:
        draw.line(mouth_pts, fill=(100, 0, 0, 255), width=4)

    # Rising heat waves from bottom
    heat_layer = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    h_draw = ImageDraw.Draw(heat_layer)
    for i in range(10):
        bx = int(pw * (i + 0.5) / 10)
        for dy in range(0, 50, 2):
            wy = ph - 5 - dy
            wx = bx + int(math.sin(t * 4 + i * 1.5 + dy * 0.12) * 8)
            a = max(0, 100 - dy * 2)
            h_draw.ellipse([wx - 1, wy - 1, wx + 1, wy + 1],
                           fill=(255, 120 + dy, 30, a))
    scene.alpha_composite(heat_layer)


# ── Overcast ─────────────────────────────────────────────────────────

def _render_overcast(scene: Image.Image, draw: ImageDraw.Draw,
                     pw: int, ph: int, t: float, pal: dict, condition: str):
    global _fog_inited

    if not _fog_inited:
        _init_fog(pw, ph)

    cloud_layer = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    c_draw = ImageDraw.Draw(cloud_layer)

    # Dense cloud ceiling — large soft puffs across the top half
    for i in range(8):
        cx = (t * (0.3 + i * 0.08) * 15 + i * pw // 5) % (pw + 400) - 200
        cy = ph * (0.1 + i * 0.04) + math.sin(t * 0.2 + i * 1.5) * 12
        rx = 200 + i * 20
        ry = 50 + i * 5
        shade = 35 + i * 4
        _draw_soft_ellipse(c_draw, int(cx), int(cy), int(rx), int(ry),
                          (shade, shade + 4, shade + 12), 30 + i * 3, steps=5)

    scene.alpha_composite(cloud_layer)

    # Lower fog puffs using same system as fog mode
    _render_fog(scene, draw, pw, ph, t, pal)


# ── Rain ─────────────────────────────────────────────────────────────

def _render_rain(scene: Image.Image, draw: ImageDraw.Draw,
                 pw: int, ph: int, t: float, pal: dict, condition: str):
    global _rain_drops, _splashes, _clouds_inited

    heavy = 'heavy' in condition.lower()
    count = 80 if heavy else 50

    # Clouds at top
    if not _clouds_inited:
        _init_clouds(pw, ph, condition)

    cloud_layer = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    c_draw = ImageDraw.Draw(cloud_layer)
    for cl in _clouds:
        cl['x'] += cl['speed']
        cw = int(pw * 0.35 * cl['scale'])
        ch = int(ph * 0.2 * cl['scale'])
        if cl['x'] > pw + cw * 0.5:
            cl['x'] = -cw
        cy = int(cl['y_frac'] * ph + math.sin(t * 0.2 + cl['phase']) * 6)
        c = pal.get('cloud', (28, 32, 48))
        _draw_cloud(c_draw, cl['x'], cy, cw, ch, c, cl['alpha'])
    scene.alpha_composite(cloud_layer)

    # Rain drops
    if not _rain_drops or len(_rain_drops) < count:
        _init_rain(pw, ph, count)

    rain_layer = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    r_draw = ImageDraw.Draw(rain_layer)

    new_splashes = []
    for drop in _rain_drops:
        drop['y'] += drop['speed']
        drop['x'] -= drop['wind']

        if drop['y'] > ph:
            # Create splash at ground
            if random.random() < 0.4:
                new_splashes.append({
                    'x': drop['x'], 'y': ph - 3,
                    'life': random.randint(2, 5),
                    'bright': drop['bright'],
                })
            drop['y'] = random.uniform(-30, -5)
            drop['x'] = random.uniform(0, pw + 20)

        if drop['x'] < -10:
            drop['x'] = pw + random.uniform(0, 20)
            drop['y'] = random.uniform(-30, -5)

        # Draw the raindrop — a thin diagonal line
        b = drop['bright']
        c_far = pal.get('drop_far', (60, 100, 180))
        c_near = pal.get('drop_near', (100, 150, 255))
        c = _lerp_color(c_far, c_near, b)
        alpha = int(80 + 150 * b)
        x1 = drop['x']
        y1 = drop['y']
        x2 = x1 - drop['wind'] * 0.7
        y2 = y1 + drop['length']
        r_draw.line([(int(x1), int(y1)), (int(x2), int(y2))],
                    fill=(c[0], c[1], c[2], alpha), width=1)

    # Splashes
    for sp in _splashes:
        sp['life'] -= 1
    _splashes = [s for s in _splashes if s['life'] > 0] + new_splashes
    for sp in _splashes[:30]:  # cap
        a = int(120 * sp['bright'] * (sp['life'] / 5.0))
        r_draw.ellipse([int(sp['x']) - 2, int(sp['y']) - 1,
                        int(sp['x']) + 2, int(sp['y']) + 1],
                       fill=(140, 180, 255, a))

    scene.alpha_composite(rain_layer)

    # Blue fog/mist at bottom where rain meets ground
    fog_layer = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    f_draw = ImageDraw.Draw(fog_layer)
    mist_c = pal.get('mist', (40, 60, 100))
    for y in range(ph - 70, ph):
        frac = (y - (ph - 70)) / 70.0
        a = int(70 * frac)
        f_draw.line([(0, y), (pw - 1, y)],
                    fill=(mist_c[0], mist_c[1], mist_c[2], a))
    scene.alpha_composite(fog_layer)


# ── Thunderstorm ─────────────────────────────────────────────────────

def _render_thunder(scene: Image.Image, draw: ImageDraw.Draw,
                    pw: int, ph: int, t: float, pal: dict):
    global _lightning_cooldown, _lightning_active, _lightning_bolt
    global _rain_drops, _splashes, _clouds_inited

    # Dense cloud ceiling
    if not _clouds_inited:
        _init_clouds(pw, ph, 'thunderstorm')

    cloud_layer = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    c_draw = ImageDraw.Draw(cloud_layer)
    for cl in _clouds:
        cl['x'] += cl['speed']
        cw = int(pw * 0.4 * cl['scale'])
        ch = int(ph * 0.25 * cl['scale'])
        if cl['x'] > pw + cw * 0.5:
            cl['x'] = -cw
        cy = int(cl['y_frac'] * ph + math.sin(t * 0.15 + cl['phase']) * 5)
        c = pal.get('cloud', (22, 18, 38))
        _draw_cloud(c_draw, cl['x'], cy, cw, ch, c, cl['alpha'])
    scene.alpha_composite(cloud_layer)

    # Dense rain
    if not _rain_drops or len(_rain_drops) < 90:
        _init_rain(pw, ph, 90)

    rain_layer = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    r_draw = ImageDraw.Draw(rain_layer)
    new_splashes = []
    for drop in _rain_drops:
        drop['y'] += drop['speed'] * 1.2
        drop['x'] -= drop['wind'] * 1.3
        if drop['y'] > ph:
            if random.random() < 0.3:
                new_splashes.append({
                    'x': drop['x'], 'y': ph - 3,
                    'life': random.randint(2, 4),
                    'bright': drop['bright'],
                })
            drop['y'] = random.uniform(-30, -5)
            drop['x'] = random.uniform(0, pw + 20)
        if drop['x'] < -10:
            drop['x'] = pw + random.uniform(0, 20)
            drop['y'] = random.uniform(-30, -5)

        dc = pal.get('drop', (80, 110, 200))
        alpha = int(60 + 120 * drop['bright'])
        r_draw.line([(int(drop['x']), int(drop['y'])),
                     (int(drop['x'] - drop['wind']),
                      int(drop['y'] + drop['length']))],
                    fill=(dc[0], dc[1], dc[2], alpha), width=1)

    for sp in _splashes:
        sp['life'] -= 1
    _splashes = [s for s in _splashes if s['life'] > 0] + new_splashes
    for sp in _splashes[:25]:
        a = int(100 * sp['bright'] * (sp['life'] / 4.0))
        r_draw.ellipse([int(sp['x']) - 2, int(sp['y']) - 1,
                        int(sp['x']) + 2, int(sp['y']) + 1],
                       fill=(120, 150, 230, a))
    scene.alpha_composite(rain_layer)

    # Lightning logic
    _lightning_cooldown -= 1
    if _lightning_active > 0:
        _lightning_active -= 1
        # Flash — illuminate the whole panel
        flash_a = int(60 * (_lightning_active / 3.0))
        flash = Image.new('RGBA', (pw, ph),
                          (pal['flash'][0], pal['flash'][1], pal['flash'][2], flash_a))
        scene.alpha_composite(flash)

        # Draw bolt
        if _lightning_bolt:
            bolt_layer = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
            b_draw = ImageDraw.Draw(bolt_layer)
            bolt_a = int(220 * (_lightning_active / 3.0))
            # Glow behind bolt
            for j in range(len(_lightning_bolt) - 1):
                b_draw.line([_lightning_bolt[j], _lightning_bolt[j + 1]],
                            fill=(150, 150, 255, bolt_a // 3), width=8)
            # Bright bolt
            for j in range(len(_lightning_bolt) - 1):
                b_draw.line([_lightning_bolt[j], _lightning_bolt[j + 1]],
                            fill=(pal['bolt'][0], pal['bolt'][1], pal['bolt'][2], bolt_a),
                            width=2)
            # Core (brightest)
            for j in range(len(_lightning_bolt) - 1):
                b_draw.line([_lightning_bolt[j], _lightning_bolt[j + 1]],
                            fill=(255, 255, 255, bolt_a), width=1)
            scene.alpha_composite(bolt_layer)

    elif _lightning_cooldown <= 0 and random.random() < 0.06:
        # Trigger new lightning
        _lightning_active = 3  # frames
        _lightning_cooldown = random.randint(15, 40)
        # Generate bolt path
        bx = random.randint(pw // 5, pw * 4 // 5)
        _lightning_bolt = [(bx, 10)]
        y_pos = 10
        for _ in range(random.randint(5, 9)):
            y_pos += random.randint(25, 55)
            bx += random.randint(-35, 35)
            bx = max(5, min(pw - 5, bx))
            _lightning_bolt.append((bx, min(y_pos, ph - 10)))
            if y_pos >= ph - 10:
                break


# ── Snow ─────────────────────────────────────────────────────────────

def _render_snow(scene: Image.Image, draw: ImageDraw.Draw,
                 pw: int, ph: int, t: float, pal: dict):
    global _snow_flakes, _clouds_inited

    # Gentle cloud layer
    if not _clouds_inited:
        _init_clouds(pw, ph, 'snow')

    cloud_layer = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    c_draw = ImageDraw.Draw(cloud_layer)
    for cl in _clouds:
        cl['x'] += cl['speed']
        cw = int(pw * 0.35 * cl['scale'])
        ch = int(ph * 0.2 * cl['scale'])
        if cl['x'] > pw + cw * 0.5:
            cl['x'] = -cw
        cy = int(cl['y_frac'] * ph + math.sin(t * 0.15 + cl['phase']) * 6)
        c = pal.get('cloud', (55, 60, 78))
        _draw_cloud(c_draw, cl['x'], cy, cw, ch, c, cl['alpha'])
    scene.alpha_composite(cloud_layer)

    # Snowflakes
    if not _snow_flakes or len(_snow_flakes) < 70:
        _init_snow(pw, ph, 70)

    snow_layer = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(snow_layer)

    fc = pal.get('flake', (220, 230, 255))
    for flake in _snow_flakes:
        flake['y'] += flake['speed']
        # Sine-wave horizontal drift
        flake['x'] += math.sin(t * 0.6 + flake['phase']) * 0.8 + flake['drift'] * 0.3

        if flake['y'] > ph + 5:
            flake['y'] = random.uniform(-15, -3)
            flake['x'] = random.uniform(0, pw)
        if flake['x'] < -10:
            flake['x'] = pw + 5
        elif flake['x'] > pw + 10:
            flake['x'] = -5

        depth = flake['depth']
        size = flake['size'] * (0.6 + 0.5 * depth)
        alpha = int(120 + 135 * depth)

        # Draw with softness: outer glow ring + bright core
        sx = int(flake['x'])
        sy = int(flake['y'])
        outer = int(size + 2)
        s_draw.ellipse([sx - outer, sy - outer, sx + outer, sy + outer],
                       fill=(fc[0], fc[1], fc[2], alpha // 2))
        inner = max(1, int(size * 0.6))
        s_draw.ellipse([sx - inner, sy - inner, sx + inner, sy + inner],
                       fill=(min(255, fc[0] + 20), min(255, fc[1] + 15),
                             min(255, fc[2]), min(255, alpha + 30)))

    scene.alpha_composite(snow_layer)

    # Soft snow accumulation hint at bottom
    acc_layer = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    a_draw = ImageDraw.Draw(acc_layer)
    for y in range(ph - 30, ph):
        frac = (y - (ph - 30)) / 30.0
        a = int(70 * frac)
        a_draw.line([(0, y), (pw - 1, y)], fill=(180, 190, 215, a))
    scene.alpha_composite(acc_layer)


# ── Fog / Mist / Haze ───────────────────────────────────────────────

def _draw_soft_ellipse(draw, cx, cy, rx, ry, color, alpha, steps=6):
    """Draw a soft-edged ellipse using concentric rings that fade outward."""
    for i in range(steps, 0, -1):
        frac = i / steps
        a = int(alpha * frac * frac)  # quadratic falloff
        if a < 2:
            continue
        srx = int(rx * (1.0 + (1.0 - frac) * 0.5))  # outer rings are bigger
        sry = int(ry * (1.0 + (1.0 - frac) * 0.5))
        draw.ellipse([cx - srx, cy - sry, cx + srx, cy + sry],
                     fill=(color[0], color[1], color[2], a))

def _render_fog(scene: Image.Image, draw: ImageDraw.Draw,
                pw: int, ph: int, t: float, pal: dict):
    global _fog_inited

    if not _fog_inited:
        _init_fog(pw, ph)

    fog_layer = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    f_draw = ImageDraw.Draw(fog_layer)

    for puff in _fog_bands:
        # Drift horizontally
        puff['x'] += puff['speed']
        # Wrap around
        if puff['x'] > pw + puff['rx']:
            puff['x'] = -puff['rx']
        elif puff['x'] < -puff['rx']:
            puff['x'] = pw + puff['rx']

        # Gentle vertical bob
        cx = puff['x']
        cy = puff['y'] + math.sin(t * 0.2 + puff['phase']) * 15
        rx = puff['rx'] + math.sin(t * 0.15 + puff['phase'] * 2) * 20
        ry = puff['ry'] + math.sin(t * 0.25 + puff['phase']) * 8

        # Opacity pulses gently
        opacity = puff['opacity'] * (1.0 + 0.2 * math.sin(t * 0.3 + puff['phase'] * 1.5))
        # Nearer puffs (higher depth) are brighter
        shade = int(50 + 30 * puff['depth'])
        color = (shade, shade + 5, shade + 15)
        alpha = int(255 * opacity * puff['depth'])
        alpha = max(4, min(40, alpha))

        _draw_soft_ellipse(f_draw, int(cx), int(cy), int(rx), int(ry),
                          color, alpha, steps=5)

    scene.alpha_composite(fog_layer)


# ── Partly Cloudy ────────────────────────────────────────────────────

def _render_partly_cloudy(scene: Image.Image, draw: ImageDraw.Draw,
                          pw: int, ph: int, t: float, pal: dict):
    global _clouds_inited

    # Sun in upper area with gentle glow
    sun_cx = int(pw * 0.7)
    sun_cy = int(ph * 0.28)
    sun_r = int(min(pw, ph) * 0.1)

    # Glow layers — high intensity for clear visibility
    _draw_glow(draw, sun_cx, sun_cy, sun_r * 5,
               pal.get('sun_glow', (255, 210, 100)), steps=18, intensity=2.0)
    _draw_glow(draw, sun_cx, sun_cy, sun_r * 2.5,
               pal.get('sun_core', (255, 240, 180)), steps=12, intensity=2.5)

    # Sun body
    draw.ellipse([sun_cx - sun_r, sun_cy - sun_r,
                  sun_cx + sun_r, sun_cy + sun_r],
                 fill=(255, 248, 210, 255))
    # Bright inner core
    ir = sun_r // 2
    draw.ellipse([sun_cx - ir, sun_cy - ir, sun_cx + ir, sun_cy + ir],
                 fill=(255, 255, 240, 255))

    # Clouds drifting, occasionally passing over sun
    if not _clouds_inited:
        _init_clouds(pw, ph, 'partly cloudy')

    cloud_layer = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    c_draw = ImageDraw.Draw(cloud_layer)

    cc = pal.get('cloud', (140, 150, 170))
    for cl in _clouds:
        cl['x'] += cl['speed']
        cw = int(pw * 0.3 * cl['scale'])
        ch = int(ph * 0.2 * cl['scale'])
        if cl['x'] > pw + cw * 0.5:
            cl['x'] = -cw
        cy = int(cl['y_frac'] * ph + math.sin(t * 0.2 + cl['phase']) * 10)
        _draw_cloud(c_draw, cl['x'], cy, cw, ch, cc, cl['alpha'])

    scene.alpha_composite(cloud_layer)


# ---------------------------------------------------------------------------
# Weather text overlay (drawn AFTER the scene, with shadows for readability)
# ---------------------------------------------------------------------------

def _draw_text_shadow(draw: ImageDraw.Draw, x: int, y: int, text: str,
                      fill: tuple, f: ImageFont.FreeTypeFont,
                      shadow_color: tuple = (0, 0, 0),
                      shadow_offset: int = 2):
    """Draw text with a dark shadow for readability over busy backgrounds."""
    # Shadow (draw at multiple offsets for thickness)
    sc = shadow_color
    for dx in range(1, shadow_offset + 1):
        for dy in range(1, shadow_offset + 1):
            draw.text((x + dx, y + dy), text, fill=sc, font=f)
    # Main text
    draw.text((x, y), text, fill=fill, font=f)


def _draw_weather_text(draw: ImageDraw.Draw, px: int, py: int, pw: int, ph: int):
    """Overlay weather data text onto the weather panel."""
    if not _weather:
        return

    margin = 12

    # Location at top
    loc = _weather.get('location', '')
    if loc:
        _draw_text_shadow(draw, px + margin, py + 10, loc,
                          (200, 210, 230), font(16), shadow_offset=1)

    # Temperature — large
    temp_f = _weather.get('temp_f')
    feels_f = _weather.get('feels_f')
    if temp_f and temp_f != '?':
        temp_str = f"{temp_f}\u00b0F"
        feels_str = f"Feels like {feels_f}\u00b0F"
    else:
        temp_str = f"{_weather.get('temp_c', '?')}\u00b0C"
        feels_str = f"Feels like {_weather.get('feels_c', '?')}\u00b0C"

    _draw_text_shadow(draw, px + margin, py + 34, temp_str,
                      (255, 255, 255), font(68, bold=True), shadow_offset=3)

    # Feels like — smaller, semi-transparent
    _draw_text_shadow(draw, px + margin, py + 112, feels_str,
                      (200, 210, 225), font(17), shadow_offset=1)

    # Condition text
    condition = _weather.get('condition', 'Unknown')
    _draw_text_shadow(draw, px + margin, py + 140, condition,
                      (240, 245, 255), font(22, bold=True), shadow_offset=2)

    # Details at bottom
    detail_y = py + 178
    detail_font = font(15)
    label_font = font(12)
    row_h = 42
    col_w = (pw - margin * 2) // 2

    humidity = _weather.get('humidity', '?')
    wind_mph = _weather.get('wind_mph', _weather.get('wind_kph', '?'))
    wind_dir = _weather.get('wind_dir', '')
    pressure = _weather.get('pressure', '?')

    details = [
        ("HUMIDITY", f"{humidity}%"),
        ("WIND", f"{wind_mph} mph {wind_dir}"),
        ("PRESSURE", f"{pressure} hPa"),
    ]

    for i, (label, value) in enumerate(details):
        col = i % 2
        row = i // 2
        dx = px + margin + col * col_w
        dy = detail_y + row * row_h
        _draw_text_shadow(draw, dx, dy, label,
                          (150, 160, 180), label_font, shadow_offset=1)
        _draw_text_shadow(draw, dx, dy + 16, value,
                          (220, 230, 245), detail_font, shadow_offset=1)


# ---------------------------------------------------------------------------
# Weather fetch
# ---------------------------------------------------------------------------

def _fetch_weather():
    """Fetch weather from wttr.in. Updates module-level state."""
    global _weather, _weather_last_fetch, _weather_error
    global _clouds_inited, _fog_inited, _grass_inited
    try:
        import requests
        resp = requests.get('http://wttr.in/?format=j1', timeout=10, headers={
            'User-Agent': 'tinyscreen-clock/2.0',
            'Accept': 'application/json',
        })
        resp.raise_for_status()
        data = resp.json()

        current = data.get('current_condition', [{}])[0]
        new_weather = {
            'temp_c':      current.get('temp_C', '?'),
            'temp_f':      current.get('temp_F', '?'),
            'feels_c':     current.get('FeelsLikeC', '?'),
            'feels_f':     current.get('FeelsLikeF', '?'),
            'condition':   current.get('weatherDesc', [{'value': 'Unknown'}])[0].get('value', 'Unknown'),
            'humidity':    current.get('humidity', '?'),
            'wind_kph':    current.get('windspeedKmph', '?'),
            'wind_mph':    current.get('windspeedMiles', '?'),
            'wind_dir':    current.get('winddir16Point', ''),
            'pressure':    current.get('pressure', '?'),
            'visibility':  current.get('visibility', '?'),
            'uv_index':    current.get('uvIndex', '?'),
            'cloud_cover': current.get('cloudcover', '?'),
        }

        nearest = data.get('nearest_area', [{}])[0]
        area = nearest.get('areaName', [{'value': ''}])[0].get('value', '')
        region = nearest.get('region', [{'value': ''}])[0].get('value', '')
        new_weather['location'] = f"{area}, {region}" if area else "Unknown"

        # Reset animation state if condition category changed
        old_type = None
        if _weather:
            old_type = _classify_condition(
                _weather.get('condition', ''), _weather.get('temp_f'))
        new_type = _classify_condition(
            new_weather.get('condition', ''), new_weather.get('temp_f'))
        if old_type != new_type:
            _clouds_inited = False
            _fog_inited = False
            _grass_inited = False

        _weather = new_weather
        _weather_error = None
        _weather_last_fetch = time.monotonic()
    except ImportError:
        _weather_error = "requests library not installed"
        _weather_last_fetch = time.monotonic()
    except Exception as e:
        _weather_error = str(e)[:60]
        _weather_last_fetch = time.monotonic()


# ---------------------------------------------------------------------------
# System info helpers
# ---------------------------------------------------------------------------

def _read_hostname() -> str:
    global _cached_hostname
    if _cached_hostname is not None:
        return _cached_hostname
    try:
        with open('/etc/hostname') as f:
            _cached_hostname = f.read().strip()
    except Exception:
        _cached_hostname = 'unknown'
    return _cached_hostname


def _read_uptime() -> str:
    try:
        with open('/proc/uptime') as f:
            secs = float(f.read().split()[0])
        days = int(secs // 86400)
        hours = int((secs % 86400) // 3600)
        mins = int((secs % 3600) // 60)
        if days > 0:
            return f"{days}d {hours}h {mins}m"
        return f"{hours}h {mins}m"
    except Exception:
        return '?'


def _read_cpu_pct() -> float:
    """Smoothed CPU usage from /proc/stat with EMA (0.7/0.3)."""
    global _cpu_smooth
    try:
        with open('/proc/stat') as f:
            line = f.readline()
        parts = line.split()
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        if not hasattr(_read_cpu_pct, '_prev'):
            _read_cpu_pct._prev = (idle, total)
            return 0.0
        d_idle = idle - _read_cpu_pct._prev[0]
        d_total = total - _read_cpu_pct._prev[1]
        _read_cpu_pct._prev = (idle, total)
        raw = 100.0 * (1.0 - d_idle / d_total) if d_total > 0 else 0.0
        _cpu_smooth = _cpu_smooth * 0.92 + raw * 0.08
        return _cpu_smooth
    except Exception:
        return 0.0


def _read_mem_pct() -> float:
    try:
        info = {}
        with open('/proc/meminfo') as f:
            for line in f:
                parts = line.split()
                info[parts[0].rstrip(':')] = int(parts[1])
        total = info['MemTotal']
        avail = info.get('MemAvailable', info.get('MemFree', 0))
        used = total - avail
        return 100.0 * used / total if total else 0
    except Exception:
        return 0.0


def _read_load() -> str:
    try:
        with open('/proc/loadavg') as f:
            parts = f.read().split()
        return f"{parts[0]} {parts[1]} {parts[2]}"
    except Exception:
        return '?'


def _read_ip() -> str:
    global _cached_ip, _cached_ip_time
    now = time.monotonic()
    if now - _cached_ip_time < 30:
        return _cached_ip
    try:
        out = subprocess.run(['hostname', '-I'], capture_output=True,
                             text=True, timeout=2)
        ips = out.stdout.strip().split()
        _cached_ip = ips[0] if ips else '--'
    except Exception:
        _cached_ip = '--'
    _cached_ip_time = now
    return _cached_ip


def _read_kernel() -> str:
    global _cached_kernel
    if _cached_kernel is not None:
        return _cached_kernel
    try:
        with open('/proc/version') as f:
            _cached_kernel = f.read().split()[2]
    except Exception:
        _cached_kernel = '?'
    return _cached_kernel


# ---------------------------------------------------------------------------
# UI drawing helpers
# ---------------------------------------------------------------------------

def _pct_color(pct: float) -> tuple:
    if pct < 50:
        return GREEN
    if pct < 75:
        return YELLOW
    if pct < 90:
        return ORANGE
    return RED


def _draw_bar(draw: ImageDraw.Draw, x: int, y: int, w: int, h: int,
              pct: float, color: tuple):
    """Draw a filled bar with gradient-like appearance."""
    # Background
    draw.rectangle([x, y, x + w, y + h], fill=(12, 14, 22), outline=BORDER)
    fill_w = max(0, int(w * min(pct, 100) / 100))
    if fill_w > 1:
        # Dimmer body
        dim = tuple(max(0, c - 80) for c in color)
        draw.rectangle([x + 1, y + 1, x + fill_w, y + h - 1], fill=dim)
        # Brighter leading edge
        edge_w = min(4, fill_w)
        draw.rectangle([x + fill_w - edge_w, y + 1,
                        x + fill_w, y + h - 1], fill=color)
        # Highlight strip at top for depth
        if fill_w > 2:
            highlight = tuple(min(255, c + 40) for c in color)
            draw.rectangle([x + 1, y + 1, x + fill_w - 1, y + 2],
                           fill=highlight)


# ---------------------------------------------------------------------------
# Panel 1: Clock
# ---------------------------------------------------------------------------

def _render_clock_panel(draw: ImageDraw.Draw, x: int, y: int, w: int, h: int):
    """Render clock panel text (background drawn separately as overlay)."""

    now_t = time.localtime()
    cx = x + w // 2  # horizontal center of panel

    # ── Time: massive 12-hour, hour:min only ──
    hour_min = time.strftime("%-I:%M", now_t)
    ampm = time.strftime("%p", now_t)
    secs = time.strftime("%S", now_t)

    time_font = font(130, bold=True)
    ampm_font = font(40, bold=True)
    sec_font = font(32)

    # Measure hour:min
    hm_bbox = draw.textbbox((0, 0), hour_min, font=time_font)
    hm_w = hm_bbox[2] - hm_bbox[0]
    hm_h = hm_bbox[3] - hm_bbox[1]

    # Measure AM/PM
    ap_bbox = draw.textbbox((0, 0), ampm, font=ampm_font)
    ap_w = ap_bbox[2] - ap_bbox[0]

    # Measure seconds
    sec_bbox = draw.textbbox((0, 0), secs, font=sec_font)
    sec_w = sec_bbox[2] - sec_bbox[0]

    # Total width: hour:min + gap + AM/PM column (AM/PM above seconds)
    right_col_w = max(ap_w, sec_w) + 8
    total_w = hm_w + right_col_w
    start_x = cx - total_w // 2
    time_y = y + 30

    # Glow effect for time: draw multiple offset copies with dim color
    glow_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(glow_layer)
    glow_c = (0, 120, 180, 30)
    for dx in range(-3, 4):
        for dy in range(-3, 4):
            if dx == 0 and dy == 0:
                continue
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > 4:
                continue
            a = int(30 * (1 - dist / 4))
            g_draw.text((start_x + dx - x, time_y + dy - y), hour_min,
                        fill=(0, 160, 220, a), font=time_font)

    # We need to composite the glow. Since we are drawing on the main image,
    # we will just draw soft shadow directly on draw (RGB mode approximation).
    for offset in [(2, 2), (-1, -1), (1, -1), (-1, 1), (2, 0), (0, 2)]:
        draw.text((start_x + offset[0], time_y + offset[1]), hour_min,
                  fill=(0, 50, 80), font=time_font)

    # Main time text
    draw.text((start_x, time_y), hour_min, fill=ACCENT, font=time_font)

    # AM/PM to the right, vertically centered with upper half
    right_x = start_x + hm_w + 8
    ap_y = time_y + 15
    draw.text((right_x, ap_y), ampm, fill=(0, 170, 230), font=ampm_font)

    # Seconds below AM/PM, smaller
    sec_y = ap_y + 48
    draw.text((right_x + 4, sec_y), secs, fill=TEXT_DIM, font=sec_font)

    # ── Date ──
    date_str = time.strftime("%A, %B %d, %Y", now_t)
    date_font_obj = font(28)
    d_bbox = draw.textbbox((0, 0), date_str, font=date_font_obj)
    dw = d_bbox[2] - d_bbox[0]
    dx = cx - dw // 2
    dy = time_y + hm_h + 50
    draw.text((dx, dy), date_str, fill=TEXT, font=date_font_obj)

    # ── Timezone ──
    tz_abbr = time.strftime("%Z", now_t)
    tz_offset = time.strftime("UTC%z", now_t)
    tz_str = f"{tz_abbr} ({tz_offset})"
    tz_font = font(18)
    tz_bbox = draw.textbbox((0, 0), tz_str, font=tz_font)
    tzw = tz_bbox[2] - tz_bbox[0]
    draw.text((cx - tzw // 2, dy + 38), tz_str, fill=TEXT_DIM, font=tz_font)


# ---------------------------------------------------------------------------
# Panel 3: System Info
# ---------------------------------------------------------------------------

def _render_sysinfo_panel(draw: ImageDraw.Draw, x: int, y: int, w: int, h: int):
    """Render system info panel with hostname, uptime, CPU, RAM, IP, load, kernel."""
    margin = 12
    sx = x + margin
    bar_w = w - margin * 2

    # Hostname — large
    hostname = _read_hostname()
    draw.text((sx, y + 14), hostname, fill=ACCENT, font=font(34, bold=True))

    # Uptime
    row_y = y + 58
    draw.text((sx, row_y), "UPTIME", fill=TEXT_DIM, font=font(12))
    draw.text((sx + 80, row_y), _read_uptime(), fill=TEXT, font=font(17))

    # CPU
    row_y += 36
    cpu_pct = _read_cpu_pct()
    cpu_color = _pct_color(cpu_pct)
    draw.text((sx, row_y), "CPU", fill=TEXT_DIM, font=font(12))
    draw.text((sx + 80, row_y - 4), f"{cpu_pct:.0f}%",
              fill=cpu_color, font=font(26, bold=True))
    bar_y = row_y + 26
    _draw_bar(draw, sx, bar_y, bar_w, 12, cpu_pct, cpu_color)

    # RAM
    row_y = bar_y + 22
    mem_pct = _read_mem_pct()
    mem_color = _pct_color(mem_pct)
    draw.text((sx, row_y), "RAM", fill=TEXT_DIM, font=font(12))
    draw.text((sx + 80, row_y - 4), f"{mem_pct:.0f}%",
              fill=mem_color, font=font(26, bold=True))
    bar_y2 = row_y + 26
    _draw_bar(draw, sx, bar_y2, bar_w, 12, mem_pct, mem_color)

    # IP
    row_y = bar_y2 + 22
    draw.text((sx, row_y), "IP", fill=TEXT_DIM, font=font(12))
    draw.text((sx + 80, row_y), _read_ip(), fill=GREEN, font=font(17))

    # Load
    row_y += 28
    draw.text((sx, row_y), "LOAD", fill=TEXT_DIM, font=font(12))
    draw.text((sx + 80, row_y), _read_load(), fill=TEXT, font=font(17))

    # Kernel
    row_y += 28
    kern = _read_kernel()
    draw.text((sx, row_y), "KERNEL", fill=TEXT_DIM, font=font(12))
    draw.text((sx + 80, row_y), kern[:30], fill=TEXT_DIM, font=font(15))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init():
    """Initialize clock state. Fetches weather on first call."""
    global _weather, _weather_last_fetch, _weather_error
    global _clouds_inited, _fog_inited, _grass_inited
    global _rain_drops, _snow_flakes, _splashes, _fog_bands
    global _lightning_cooldown, _lightning_active, _anim_frame
    _weather = None
    _weather_last_fetch = 0.0
    _weather_error = None
    _clouds_inited = False
    _fog_inited = False
    _grass_inited = False
    _rain_drops = []
    _snow_flakes = []
    _splashes = []
    _fog_bands = []
    _lightning_cooldown = 0
    _lightning_active = 0
    _anim_frame = 0
    _fetch_weather()


def render_frame(w: int = 1920, h: int = 440) -> Image.Image:
    """Render one frame of the clock dashboard. Returns PIL Image (RGB)."""
    now = time.monotonic()

    # Periodic weather refresh
    if now - _weather_last_fetch >= WEATHER_INTERVAL:
        _fetch_weather()

    pad = 6
    py0 = pad
    ph = h - pad * 2
    p1w = int(w * 0.40)
    p2x = pad + p1w + pad
    p2w = int(w * 0.30)
    p3x = p2x + p2w + pad
    p3w = w - p3x - pad

    # ═══════════════════════════════════════════════════════════════
    # Step 1: Render weather atmosphere across the FULL screen
    # ═══════════════════════════════════════════════════════════════
    condition = ''
    temp_f = None
    if _weather:
        condition = _weather.get('condition', 'Unknown')
        temp_f = _weather.get('temp_f')

    if condition:
        try:
            scene = _render_weather_scene(w, h, condition, temp_f)
            img = Image.new('RGB', (w, h), BG)
            img.paste(scene, (0, 0), scene)
        except Exception:
            img = Image.new('RGB', (w, h), BG)
    else:
        img = Image.new('RGB', (w, h), BG)

    draw = ImageDraw.Draw(img)

    # ═══════════════════════════════════════════════════════════════
    # Step 2: Draw semi-transparent panel backgrounds over the weather
    # ═══════════════════════════════════════════════════════════════
    panel_overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    p_draw = ImageDraw.Draw(panel_overlay)
    # Clock panel — darker so text is readable
    p_draw.rectangle([pad, py0, pad + p1w, py0 + ph], fill=(10, 12, 18, 200))
    # System panel — darker so text is readable
    p_draw.rectangle([p3x, py0, p3x + p3w, py0 + ph], fill=(10, 12, 18, 200))
    # Weather panel — just a subtle darkening, let the weather show through
    p_draw.rectangle([p2x, py0, p2x + p2w, py0 + ph], fill=(10, 12, 18, 60))
    img.paste(Image.alpha_composite(Image.new('RGBA', (w, h), (0,0,0,0)), panel_overlay), (0, 0), panel_overlay)

    # Panel borders
    for (px_s, pw_s) in [(pad, p1w), (p2x, p2w), (p3x, p3w)]:
        draw.rectangle([px_s, py0, px_s + pw_s, py0], fill=ACCENT_DIM)
        draw.line([(px_s, py0), (px_s, py0+ph)], fill=BORDER)
        draw.line([(px_s+pw_s, py0), (px_s+pw_s, py0+ph)], fill=BORDER)
        draw.line([(px_s, py0+ph), (px_s+pw_s, py0+ph)], fill=BORDER)

    # ═══════════════════════════════════════════════════════════════
    # Step 3: Draw content on top
    # ═══════════════════════════════════════════════════════════════
    _render_clock_panel(draw, pad, py0, p1w, ph)

    if _weather:
        _draw_weather_text(draw, p2x, py0, p2w, ph)
    elif _weather_error:
        draw.text((p2x + 12, py0 + 40), "Weather unavailable",
                  fill=RED, font=font(20))
        draw.text((p2x + 12, py0 + 70), _weather_error,
                  fill=TEXT_DIM, font=font(14))
        remaining = max(0, int(WEATHER_INTERVAL - (now - _weather_last_fetch)))
        draw.text((p2x + 12, py0 + 95), f"Retry in {remaining}s",
                  fill=TEXT_DIM, font=font(14))
    else:
        draw.text((p2x + 12, py0 + 40), "Fetching weather...",
                  fill=TEXT_DIM, font=font(20))

    _render_sysinfo_panel(draw, p3x, py0, p3w, ph)

    # Bottom accent line
    draw.rectangle([0, h - 2, w, h], fill=ACCENT_DIM)

    return img


# ---------------------------------------------------------------------------
# Standalone mode
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import sys
    if '--once' in sys.argv:
        init()
        time.sleep(0.1)
        _read_cpu_pct()  # warm up CPU delta
        time.sleep(0.5)
        img = render_frame()
        img.save('/tmp/clock.png')
        print("Saved to /tmp/clock.png")
    elif '--anim' in sys.argv:
        init()
        time.sleep(0.1)
        _read_cpu_pct()
        time.sleep(0.3)
        print("Rendering 30 frames...")
        for i in range(30):
            img = render_frame()
            img.save(f'/tmp/clock_{i:03d}.png')
        print("Saved /tmp/clock_000.png through /tmp/clock_029.png")
    else:
        print("Usage:")
        print("  python3 clock.py --once    # save one frame to /tmp/clock.png")
        print("  python3 clock.py --anim    # save 30 frames for animation preview")
        print("  Use via tinyscreen daemon for live display")
