"""Cached glow texture utilities for tinyscreen display modes.

Every glow effect is computed once via GaussianBlur, then cached and
reused via fast Image.paste() calls. This gives full visual quality
at near-zero per-frame CPU cost.

Usage:
    from modes.glow import glow_rect, glow_circle, glow_text, glow_line
"""

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_cache = {}
_MAX_CACHE = 2048  # evict oldest entries if cache grows too large


def _get(key, builder):
    """Get a cached glow texture, or build and cache it."""
    if key in _cache:
        return _cache[key]
    if len(_cache) > _MAX_CACHE:
        # Drop oldest quarter of cache
        for old_key in list(_cache.keys())[:_MAX_CACHE // 4]:
            del _cache[old_key]
    result = builder()
    _cache[key] = result
    return result


def glow_rect(w, h, color, alpha=100, radius=8):
    """Cached rectangular glow texture. Returns (image, pad) tuple."""
    if w < 1 or h < 1:
        return None, 0
    pad = radius * 2
    key = ('rect', w, h, color, alpha, radius)
    def build():
        img = Image.new('RGBA', (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([pad, pad, pad + w, pad + h], fill=color + (alpha,))
        return img.filter(ImageFilter.GaussianBlur(radius=radius))
    return _get(key, build), pad


def glow_circle(r, color, alpha=60, radius=5):
    """Cached circular glow texture. Returns (image, offset_to_center)."""
    pad = radius * 2
    size = (r + pad) * 2
    key = ('circle', r, color, alpha, radius)
    def build():
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        center = size // 2
        d.ellipse([center - r - 3, center - r - 3,
                   center + r + 3, center + r + 3], fill=color + (alpha,))
        return img.filter(ImageFilter.GaussianBlur(radius=radius))
    return _get(key, build), size // 2


def glow_text(text, font_obj, color, alpha=90, radius=5):
    """Cached text glow texture. Returns (image, pad, bbox)."""
    bbox = font_obj.getbbox(text)
    tw = bbox[2] - bbox[0] + 20
    th = bbox[3] - bbox[1] + 20
    pad = radius * 2
    key = ('text', text, id(font_obj), font_obj.size, color, alpha, radius)
    def build():
        img = Image.new('RGBA', (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.text((pad - bbox[0], pad - bbox[1]), text, fill=color + (alpha,),
               font=font_obj)
        return img.filter(ImageFilter.GaussianBlur(radius=radius))
    return _get(key, build), pad, bbox


def glow_line(points, w, h, color, alpha=140, width=5, radius=6):
    """Cached sparkline/chart glow. Returns (image, pad).
    Points should be relative to (0,0) origin."""
    pad = radius + 4
    # Use point hash as cache key
    key = ('line', tuple(points), w, h, color, alpha, width, radius)
    def build():
        img = Image.new('RGBA', (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        shifted = [(px + pad, py + pad) for px, py in points]
        d.line(shifted, fill=color + (alpha,), width=width)
        return img.filter(ImageFilter.GaussianBlur(radius=radius))
    return _get(key, build), pad


def glow_arc(radius, thickness, start_angle, end_angle, color,
             alpha=80, blur_radius=10, extra_width=14):
    """Cached arc glow texture (for gauges). Returns (image, pad)."""
    pad = blur_radius * 2 + 4
    size = radius * 2 + pad * 2
    key = ('arc', radius, thickness, start_angle, end_angle, color,
           alpha, blur_radius, extra_width)
    def build():
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        bbox = [pad, pad, pad + radius * 2, pad + radius * 2]
        d.arc(bbox, start_angle, end_angle,
              fill=color + (alpha,), width=thickness + extra_width)
        return img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    return _get(key, build), pad


def glow_accent_line(w, color, alpha=200, radius=4):
    """Cached horizontal accent line glow. Returns (image, None)."""
    key = ('accent', w, color, alpha, radius)
    def build():
        h = radius * 4
        img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, w, 2], fill=color + (alpha,))
        d.rectangle([0, 2, w, 6], fill=color + (alpha // 4,))
        return img.filter(ImageFilter.GaussianBlur(radius=radius))
    return _get(key, build)


def glow_side_edge(h, color, alpha=30, radius=3):
    """Cached vertical side edge glow. Returns image."""
    w = 12
    key = ('side', h, color, alpha, radius)
    def build():
        img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.line([(w // 2, 0), (w // 2, h)], fill=color + (alpha,))
        return img.filter(ImageFilter.GaussianBlur(radius=radius))
    return _get(key, build)


def paste_glow_dot(img, cx, cy, r, color):
    """Draw a status dot with cached glow halo."""
    glow, _ = glow_circle(r, color, alpha=60, radius=5)
    img.paste(glow, (cx - r - 10, cy - r - 10), glow)
    draw = ImageDraw.Draw(img)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (255,))
    bright = tuple(min(255, c + 80) for c in color)
    core = max(1, r - 2)
    draw.ellipse([cx - core, cy - core, cx + core, cy + core],
                 fill=bright + (200,))


def paste_hero_text(draw, img, x, y, text, color, font_obj, radius=5):
    """Draw large text with cached glow behind it."""
    glow_img, pad, bbox = glow_text(text, font_obj, color, alpha=90,
                                     radius=radius)
    img.paste(glow_img, (x - pad, y - pad), glow_img)
    draw.text((x, y), text, fill=color, font=font_obj)


def paste_glow_bar(draw, img, x, y, w, h, pct, color, lerp_fn, radius=8):
    """Draw a progress bar with cached glow halo and gradient fill."""
    draw.rectangle([x, y, x + w, y + h], fill=(12, 15, 22))
    fill_w = max(0, int(w * min(pct, 100.0) / 100.0))
    if fill_w <= 0:
        return
    # Cached glow halo
    glow, pad = glow_rect(fill_w, h, color, alpha=100, radius=radius)
    if glow:
        img.paste(glow, (x - pad, y - pad), glow)
    # Gradient fill
    for col in range(fill_w):
        t = col / max(fill_w - 1, 1)
        c = lerp_fn(tuple(max(0, v - 80) for v in color), color, t * 0.8 + 0.2)
        draw.line([(x + 1 + col, y + 1), (x + 1 + col, y + h - 1)], fill=c)
    # Bright leading edge
    tip_w = min(4, fill_w)
    bright = tuple(min(255, c + 60) for c in color)
    draw.rectangle([x + fill_w - tip_w, y, x + fill_w, y + h], fill=bright)
    # Top highlight
    hl = tuple(min(255, c + 100) for c in color)
    draw.line([(x + 1, y + 1), (x + fill_w - 1, y + 1)], fill=hl)


def paste_panel(draw, img, x, y, w, h, accent_color, lerp_fn):
    """Draw a panel with gradient fill and cached glowing edges."""
    # Panel body gradient
    top_c = (14, 18, 30)
    bot_c = (8, 11, 20)
    for row in range(h):
        t = row / max(h - 1, 1)
        c = lerp_fn(top_c, bot_c, t)
        draw.line([(x, y + row), (x + w, y + row)], fill=c)
    # Cached accent line
    accent = glow_accent_line(w, accent_color)
    img.paste(accent, (x, y - 2), accent)
    # Cached side edges
    side = glow_side_edge(h, accent_color)
    img.paste(side, (x - 6, y), side)
    img.paste(side, (x + w - 6, y), side)
