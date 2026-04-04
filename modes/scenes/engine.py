"""Shared rendering engine for weather scenes.

Provides particle systems, bloom, glow sprites, gradients, color grading,
noise fields, and text shadow helpers. All GPU-free, CPU-side PIL + numpy.
"""

import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------------------
# Font cache
# ---------------------------------------------------------------------------
_fonts: dict = {}
_font_regular: str | None = None
_font_bold: str | None = None

_FONT_PATHS = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf',
]


def _resolve_fonts():
    global _font_regular, _font_bold
    if _font_regular is not None:
        return
    import os
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
# Color utilities
# ---------------------------------------------------------------------------

def lerp_color(c1: tuple, c2: tuple, t: float) -> tuple:
    """Linearly interpolate between two RGB(A) tuples."""
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def atmospheric_color(base_color: tuple, ambient_color: tuple, depth: float) -> tuple:
    """Shift color toward ambient based on depth. depth 0=near, 1=far."""
    fog = (1.0 - depth) ** 2
    return tuple(int(b * (1 - fog) + a * fog) for b, a in zip(base_color, ambient_color))


# ---------------------------------------------------------------------------
# Particle pool (numpy-backed)
# ---------------------------------------------------------------------------

class ParticlePool:
    """Fixed-size numpy-backed particle pool for vectorized updates."""

    def __init__(self, max_count: int):
        self.max = max_count
        self.x = np.zeros(max_count, dtype=np.float32)
        self.y = np.zeros(max_count, dtype=np.float32)
        self.vx = np.zeros(max_count, dtype=np.float32)
        self.vy = np.zeros(max_count, dtype=np.float32)
        self.depth = np.zeros(max_count, dtype=np.float32)
        self.size = np.zeros(max_count, dtype=np.float32)
        self.alpha = np.zeros(max_count, dtype=np.float32)
        self.life = np.zeros(max_count, dtype=np.float32)
        self.phase = np.zeros(max_count, dtype=np.float32)
        self.active = np.ones(max_count, dtype=bool)

    def update(self, dt: float = 1.0):
        """Vectorized position update."""
        self.x += self.vx * dt
        self.y += self.vy * dt

    def get_sorted_indices(self) -> np.ndarray:
        """Return indices sorted by depth (back-to-front) for drawing."""
        return np.argsort(self.depth)


# ---------------------------------------------------------------------------
# Gradient fill (numpy vectorized)
# ---------------------------------------------------------------------------

_gradient_cache: dict = {}


def gradient_fill(w: int, h: int, top: tuple, bot: tuple) -> Image.Image:
    """Create an RGBA image with a smooth vertical gradient. Cached."""
    key = (w, h, top, bot)
    if key in _gradient_cache:
        return _gradient_cache[key].copy()
    t = np.linspace(0, 1, h, dtype=np.float32).reshape(-1, 1, 1)
    top_a = np.array(top + (255,), dtype=np.float32).reshape(1, 1, 4)
    bot_a = np.array(bot + (255,), dtype=np.float32).reshape(1, 1, 4)
    grad = np.broadcast_to(top_a + (bot_a - top_a) * t, (h, w, 4))
    result = Image.fromarray(grad.astype(np.uint8), 'RGBA')
    _gradient_cache[key] = result
    return result.copy()


# ---------------------------------------------------------------------------
# Bloom (GaussianBlur at reduced resolution)
# ---------------------------------------------------------------------------

def bloom(source: Image.Image, radius: int = 20, intensity: float = 1.5,
          downsample: int = 4) -> Image.Image:
    """Create a bloom glow layer from an RGBA image.

    Downsamples, blurs, upsamples. Much faster than full-res blur.
    """
    w, h = source.size
    sw, sh = max(1, w // downsample), max(1, h // downsample)
    small = source.resize((sw, sh), Image.BILINEAR)
    effective_r = max(1, radius // downsample)
    blurred = small.filter(ImageFilter.GaussianBlur(radius=effective_r))
    result = blurred.resize((w, h), Image.BILINEAR)
    if intensity != 1.0:
        arr = np.array(result, dtype=np.float32)
        arr[..., 3] = np.clip(arr[..., 3] * intensity, 0, 255)
        result = Image.fromarray(arr.astype(np.uint8), 'RGBA')
    return result


def multi_bloom(source: Image.Image,
                passes: list[tuple[int, float]] = None) -> Image.Image:
    """Multi-pass bloom for realistic light falloff.

    passes: list of (radius, intensity) tuples.
    Default: tight core + medium halo + wide ambient.
    """
    if passes is None:
        passes = [(10, 1.0), (30, 0.6), (80, 0.3)]
    result = Image.new('RGBA', source.size, (0, 0, 0, 0))
    for radius, intensity in passes:
        glow = bloom(source, radius=radius, intensity=intensity)
        result.alpha_composite(glow)
    return result


# ---------------------------------------------------------------------------
# Glow sprites (pre-rendered, cached)
# ---------------------------------------------------------------------------

_sprite_cache: dict = {}


def glow_sprite(radius: int, color: tuple, alpha_peak: int = 180) -> Image.Image:
    """Get a pre-rendered radial glow sprite. Cached by key."""
    key = (radius, color, alpha_peak)
    if key in _sprite_cache:
        return _sprite_cache[key]
    size = radius * 2 + 1
    cy, cx = np.ogrid[-radius:radius + 1, -radius:radius + 1]
    dist = np.sqrt(cx * cx + cy * cy, dtype=np.float32) / max(radius, 1)
    dist = np.clip(dist, 0, 1)
    alpha = ((1 - dist) ** 2 * alpha_peak).astype(np.uint8)
    arr = np.zeros((size, size, 4), dtype=np.uint8)
    arr[..., 0] = color[0]
    arr[..., 1] = color[1]
    arr[..., 2] = color[2]
    arr[..., 3] = alpha
    sprite = Image.fromarray(arr, 'RGBA')
    _sprite_cache[key] = sprite
    return sprite


def stamp_glow(target: Image.Image, x: int, y: int, sprite: Image.Image):
    """Paste a glow sprite centered at (x, y)."""
    hw, hh = sprite.size[0] // 2, sprite.size[1] // 2
    target.alpha_composite(sprite, dest=(x - hw, y - hh))


# ---------------------------------------------------------------------------
# Additive compositing (for light effects)
# ---------------------------------------------------------------------------

def additive_composite(base: Image.Image, overlay: Image.Image) -> Image.Image:
    """Additive blending: light adds to light, clamped at 255.

    If base is RGBA, overlay's alpha modulates its contribution.
    Falls back to normal alpha_composite if overlay is mostly transparent.
    """
    over_arr = np.array(overlay)
    # Skip if overlay is essentially empty
    if over_arr[..., 3].max() < 2:
        return base
    base_arr = np.array(base)
    over_alpha = over_arr[..., 3:4].astype(np.float32) * (1.0 / 255.0)
    result = base_arr.copy()
    result[..., :3] = np.clip(
        base_arr[..., :3].astype(np.uint16)
        + (over_arr[..., :3].astype(np.uint16) * over_alpha).astype(np.uint16),
        0, 255).astype(np.uint8)
    return Image.fromarray(result, base.mode)


# ---------------------------------------------------------------------------
# Soft ellipse drawing
# ---------------------------------------------------------------------------

def draw_soft_ellipse(draw: ImageDraw.Draw, cx: int, cy: int,
                      rx: int, ry: int, color: tuple, alpha: int,
                      steps: int = 5):
    """Draw a soft-edged ellipse using concentric rings that fade outward."""
    for i in range(steps, 0, -1):
        frac = i / steps
        a = int(alpha * frac * frac)
        if a < 2:
            continue
        srx = int(rx * (1.0 + (1.0 - frac) * 0.5))
        sry = int(ry * (1.0 + (1.0 - frac) * 0.5))
        draw.ellipse([cx - srx, cy - sry, cx + srx, cy + sry],
                     fill=(color[0], color[1], color[2], a))


# ---------------------------------------------------------------------------
# Cloud drawing
# ---------------------------------------------------------------------------

_cloud_sprite_cache: dict = {}


def render_cloud_sprite(cw: int, ch: int, color: tuple, alpha: int = 200,
                        seed: int = 0) -> Image.Image:
    """Pre-render a volumetric cloud sprite with organic shape and lighting.

    Returns an RGBA image of size (cw, ch) with a soft, puffy cloud.
    Uses noise-modulated density for organic shape, vertical lighting
    gradient (bright top = sun-lit, dark bottom = shadowed), and
    gaussian blur for perfectly soft edges.
    """
    key = (cw, ch, color, alpha, seed)
    if key in _cloud_sprite_cache:
        return _cloud_sprite_cache[key]

    rng = random.Random(seed)

    # Work at half resolution for speed, upscale at the end
    sw, sh = max(8, cw // 2), max(8, ch // 2)
    density = np.zeros((sh, sw), dtype=np.float32)

    # Build cloud shape from ~25 overlapping radial blobs with random sizes
    # Each blob is a smooth radial falloff -- no hard edges
    ys = np.arange(sh, dtype=np.float32)
    xs = np.arange(sw, dtype=np.float32)
    yy, xx = np.meshgrid(ys, xs, indexing='ij')

    # Main body blobs -- large, centered
    blobs = [
        (0.50, 0.55, 0.40, 0.38),  # big center
        (0.35, 0.50, 0.30, 0.35),  # left body
        (0.65, 0.50, 0.32, 0.32),  # right body
        (0.50, 0.45, 0.35, 0.30),  # upper center
    ]
    # Top puffs -- the characteristic bubbly silhouette
    for i in range(8):
        bx = 0.2 + rng.random() * 0.6
        by = 0.2 + rng.random() * 0.25
        brx = 0.10 + rng.random() * 0.14
        bry = 0.12 + rng.random() * 0.16
        blobs.append((bx, by, brx, bry))
    # Side wisps
    for i in range(6):
        bx = rng.choice([rng.uniform(0.05, 0.25), rng.uniform(0.75, 0.95)])
        by = 0.35 + rng.random() * 0.3
        brx = 0.08 + rng.random() * 0.10
        bry = 0.10 + rng.random() * 0.14
        blobs.append((bx, by, brx, bry))
    # Fill blobs -- denser middle
    for i in range(6):
        bx = 0.3 + rng.random() * 0.4
        by = 0.3 + rng.random() * 0.35
        brx = 0.12 + rng.random() * 0.15
        bry = 0.14 + rng.random() * 0.16
        blobs.append((bx, by, brx, bry))

    for bx_f, by_f, brx_f, bry_f in blobs:
        bcx = sw * bx_f
        bcy = sh * by_f
        brx = max(3, sw * brx_f)
        bry = max(3, sh * bry_f)
        # Smooth radial falloff: (1 - normalized_distance^2)^2
        dx = (xx - bcx) / brx
        dy = (yy - bcy) / bry
        d2 = dx * dx + dy * dy
        blob = np.clip(1.0 - d2, 0, 1) ** 2
        density += blob

    # Normalize to 0-1
    d_max = density.max()
    if d_max > 0:
        density /= d_max

    # Threshold + smooth falloff for cloud edge
    density = np.clip((density - 0.15) / 0.85, 0, 1)

    # Vertical lighting gradient: bright at top, darker at bottom
    vert_light = np.linspace(1.3, 0.7, sh, dtype=np.float32).reshape(-1, 1)

    # Build RGBA cloud image
    arr = np.zeros((sh, sw, 4), dtype=np.float32)
    base_r, base_g, base_b = float(color[0]), float(color[1]), float(color[2])

    arr[..., 0] = np.clip(base_r * vert_light, 0, 255)
    arr[..., 1] = np.clip(base_g * vert_light, 0, 255)
    arr[..., 2] = np.clip(base_b * vert_light, 0, 255)
    arr[..., 3] = density * alpha

    # Convert to image and upscale (bilinear gives additional smoothing)
    small = Image.fromarray(arr.astype(np.uint8), 'RGBA')
    result = small.resize((cw, ch), Image.BILINEAR)

    # Final gaussian blur for perfectly soft edges
    result = result.filter(ImageFilter.GaussianBlur(radius=3))

    _cloud_sprite_cache[key] = result
    return result


def draw_cloud(draw: ImageDraw.Draw, x: float, y: float, w: float, h: float,
               color: tuple, alpha: int = 180):
    """Draw a volumetric puffy cloud at position (x, y).

    Note: `draw` parameter is the ImageDraw of the target layer.
    We composite a pre-rendered cloud sprite onto draw._im (the parent Image).
    """
    cw, ch = max(16, int(w)), max(16, int(h))
    # Use position-based seed for consistent shape per cloud
    seed = hash((int(w), int(h))) & 0x7FFFFFFF
    sprite = render_cloud_sprite(cw, ch, color, alpha, seed)
    # Composite onto the image that owns this draw context
    target = draw._image if hasattr(draw, '_image') else draw.im
    # PIL ImageDraw doesn't expose parent image easily, so we paste directly
    try:
        draw._image.alpha_composite(sprite, dest=(int(x), int(y)))
    except (AttributeError, ValueError):
        # Fallback: draw as simple filled ellipses
        lobe_alpha = max(15, alpha // 5)
        for fx, fy, frx, fry in [(0.5,0.5,0.4,0.35),(0.3,0.45,0.25,0.3),
                                   (0.7,0.45,0.25,0.3),(0.45,0.3,0.2,0.25),
                                   (0.6,0.32,0.18,0.22)]:
            cx = int(x + w * fx)
            cy = int(y + h * fy)
            rx = int(w * frx)
            ry = int(h * fry)
            draw.ellipse([cx-rx, cy-ry, cx+rx, cy+ry],
                         fill=(color[0], color[1], color[2], lobe_alpha))


# ---------------------------------------------------------------------------
# Noise field (layered-sine approximation, no dependencies)
# ---------------------------------------------------------------------------

_noise_cache: dict = {}
_noise_cache_max = 8


def noise_field(w: int, h: int, t: float, scale: float = 0.005,
                octaves: int = 3, downsample: int = 16) -> np.ndarray:
    """Generate animated 2D noise field using layered sines.

    Returns float32 array (h, w) with values in [0, 1].
    Works at 1/downsample resolution internally, upscaled with bilinear.
    """
    # Interpolate between two quantized frames for smooth transitions
    t_step = 0.4  # time step between cached frames
    t_low = math.floor(t / t_step) * t_step
    t_high = t_low + t_step
    frac = (t - t_low) / t_step  # 0..1 blend factor

    def _compute_noise(t_val):
        cache_key = (w, h, round(t_val, 4), scale, octaves, downsample)
        if cache_key in _noise_cache:
            return _noise_cache[cache_key]
        result = _compute_noise_inner(w, h, t_val, scale, octaves, downsample)
        if len(_noise_cache) >= _noise_cache_max:
            _noise_cache.pop(next(iter(_noise_cache)))
        _noise_cache[cache_key] = result
        return result

    if frac < 0.01:
        return _compute_noise(t_low)
    if frac > 0.99:
        return _compute_noise(t_high)
    a = _compute_noise(t_low)
    b = _compute_noise(t_high)
    return a * (1 - frac) + b * frac


def _compute_noise_inner(w, h, t, scale, octaves, downsample):
    sw, sh = max(4, w // downsample), max(4, h // downsample)
    xs = np.arange(sw, dtype=np.float32) * scale * downsample
    ys = np.arange(sh, dtype=np.float32) * scale * downsample
    xx, yy = np.meshgrid(xs, ys)

    # Use many irrational-ratio frequencies to avoid periodic tiling.
    # Each "octave" sums 4 sine terms with non-repeating phase offsets.
    field = np.zeros((sh, sw), dtype=np.float32)
    _irrationals = [1.0, 1.6180339, 2.2360679, 3.1415926, 2.7182818,
                    1.4142135, 1.7320508, 2.6457513]
    for octave in range(octaves):
        freq = (1.7 ** octave)  # non-power-of-2 frequency scaling
        amp = 0.55 ** octave
        ir = _irrationals
        field += amp * (
            np.sin(xx * freq * ir[0] + yy * freq * ir[3] * 0.3
                   + t * 0.3 + octave * ir[5] * 3) * 0.4
            + np.cos(yy * freq * ir[1] + xx * freq * ir[4] * 0.2
                     + t * 0.22 + octave * ir[6] * 2) * 0.3
            + np.sin((xx * ir[2] + yy * ir[7]) * freq * 0.7
                     + t * 0.17 + octave * ir[0] * 4) * 0.2
            + np.cos((xx * ir[6] - yy * ir[5]) * freq * 0.5
                     + t * 0.13 + octave * ir[3] * 5) * 0.1
        )

    # Normalize to [0, 1]
    mn, mx = field.min(), field.max()
    if mx - mn > 1e-6:
        field = (field - mn) / (mx - mn)
    else:
        field = np.full_like(field, 0.5)

    # Upscale with bilinear interpolation
    small_img = Image.fromarray((field * 255).astype(np.uint8), mode='L')
    full_img = small_img.resize((w, h), Image.BILINEAR)
    return np.array(full_img, dtype=np.float32) / 255.0


# ---------------------------------------------------------------------------
# Color grading (final post-process)
# ---------------------------------------------------------------------------

GRADE_PRESETS = {
    'rain':          {'tint': (20, 30, 60),  'strength': 0.15, 'contrast': 1.05, 'vignette': 0.15},
    'thunder':       {'tint': (15, 10, 40),  'strength': 0.20, 'contrast': 1.10, 'vignette': 0.20},
    'snow':          {'tint': (30, 35, 50),  'strength': 0.10, 'contrast': 0.95, 'vignette': 0.10},
    'overcast':      {'tint': (25, 28, 35),  'strength': 0.15, 'contrast': 0.90, 'vignette': 0.15},
    'fog':           {'tint': (30, 32, 38),  'strength': 0.20, 'contrast': 0.85, 'vignette': 0.25},
    'sunny':         {'tint': (60, 45, 15),  'strength': 0.10, 'contrast': 1.05, 'vignette': 0.10},
    'hot':           {'tint': (60, 15, 0),   'strength': 0.15, 'contrast': 1.15, 'vignette': 0.15},
    'partly_cloudy': {'tint': (30, 35, 50),  'strength': 0.08, 'contrast': 1.00, 'vignette': 0.08},
}


_vignette_cache: dict = {}


def color_grade(img: Image.Image, scene_type: str) -> Image.Image:
    """Apply cinematic color grading as final post-process."""
    grade = GRADE_PRESETS.get(scene_type)
    if not grade:
        return img

    arr = np.array(img, dtype=np.float32)
    h, w = arr.shape[:2]

    # Color tint
    tint = np.array(grade['tint'], dtype=np.float32)
    s = grade['strength']
    arr[..., :3] = arr[..., :3] * (1 - s) + tint * s * (arr[..., :3] / 255.0)

    # Contrast around midpoint
    c = grade['contrast']
    arr[..., :3] = (arr[..., :3] - 127.5) * c + 127.5

    # Elliptical vignette (cached per resolution+type)
    v = grade['vignette']
    if v > 0:
        vig_key = (w, h, v)
        if vig_key not in _vignette_cache:
            x = np.linspace(-1, 1, w, dtype=np.float32)
            y = np.linspace(-1, 1, h, dtype=np.float32)
            X, Y = np.meshgrid(x, y)
            vig = 1.0 - v * (X ** 2 * 0.3 + Y ** 2 * 0.7)
            _vignette_cache[vig_key] = np.clip(vig, 0.5, 1.0)
        arr[..., :3] *= _vignette_cache[vig_key][..., np.newaxis]

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), img.mode)


# ---------------------------------------------------------------------------
# Text rendering with heavy drop shadow
# ---------------------------------------------------------------------------

def draw_text_shadow(draw: ImageDraw.Draw, x: int, y: int, text: str,
                     fill: tuple, f: ImageFont.FreeTypeFont,
                     shadow_offset: int = 3,
                     shadow_color: tuple = (0, 0, 0)):
    """Draw text with a thick dark shadow for readability over busy scenes."""
    sc = shadow_color
    for dx in range(1, shadow_offset + 1):
        for dy in range(1, shadow_offset + 1):
            draw.text((x + dx, y + dy), text, fill=sc, font=f)
    # Extra shadow passes for thickness
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, fill=sc, font=f)
    draw.text((x, y), text, fill=fill, font=f)


# ---------------------------------------------------------------------------
# Lightning generation (midpoint displacement)
# ---------------------------------------------------------------------------

def generate_lightning(x_start: int, y_start: int, x_end: int, y_end: int,
                       generations: int = 3, spread: float = 0.25,
                       branch_prob: float = 0.2) -> list[list[tuple]]:
    """Generate branching lightning bolt via midpoint displacement.

    Returns list of polylines (main bolt + branches).
    """
    def subdivide(p1, p2, gen, offset):
        if gen == 0:
            return [p1, p2]
        mx = (p1[0] + p2[0]) / 2
        my = (p1[1] + p2[1]) / 2
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        length = math.sqrt(dx * dx + dy * dy)
        if length < 1:
            return [p1, p2]
        nx, ny = -dy / length, dx / length
        displacement = random.gauss(0, offset)
        mid = (mx + nx * displacement, my + ny * displacement)
        left = subdivide(p1, mid, gen - 1, offset * 0.55)
        right = subdivide(mid, p2, gen - 1, offset * 0.55)
        return left + right[1:]

    initial_offset = math.sqrt(
        (x_end - x_start) ** 2 + (y_end - y_start) ** 2) * spread
    main = subdivide((x_start, y_start), (x_end, y_end),
                     generations, initial_offset)
    bolts = [main]

    for i in range(1, len(main) - 1):
        if random.random() < branch_prob:
            bp = main[i]
            angle = random.uniform(-1.2, 1.2)
            blen = random.uniform(30, 100) * 0.6
            bend = (bp[0] + math.sin(angle) * blen, bp[1] + blen * 0.7)
            branch = subdivide(bp, bend, max(1, generations - 2),
                               initial_offset * 0.3)
            bolts.append(branch)

    return bolts


def draw_lightning(target: Image.Image, bolts: list[list[tuple]],
                   intensity: float = 1.0) -> Image.Image:
    """Draw lightning with subtle glow + bright body + white-hot core."""
    bolt_layer = Image.new('RGBA', target.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(bolt_layer)
    a = intensity

    for seg in bolts:
        pts = [(int(p[0]), int(p[1])) for p in seg]
        if len(pts) < 2:
            continue
        # Pass 1: soft glow (thin)
        for i in range(len(pts) - 1):
            draw.line([pts[i], pts[i + 1]],
                      fill=(100, 100, 255, int(30 * a)), width=6)
        # Pass 2: bright body
        for i in range(len(pts) - 1):
            draw.line([pts[i], pts[i + 1]],
                      fill=(180, 180, 255, int(140 * a)), width=2)
        # Pass 3: white-hot core
        for i in range(len(pts) - 1):
            draw.line([pts[i], pts[i + 1]],
                      fill=(255, 255, 255, int(240 * a)), width=1)

    # Gentle bloom for organic glow
    bloomed = bloom(bolt_layer, radius=8, intensity=1.0, downsample=4)
    target.alpha_composite(bloomed)
    target.alpha_composite(bolt_layer)
    return target
