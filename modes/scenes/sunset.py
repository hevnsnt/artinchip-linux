"""Sunset / golden hour weather scene -- dramatic, warm, cinematic.

Rich purple-to-orange sky gradient, a massive orange-red sun partially
below the horizon with wide warm bloom, dramatic god rays, dark
silhouette clouds with bright orange-pink rim lighting, intense horizon
glow, atmospheric haze, and warm golden color grading.  Designed to feel
like a breathtaking golden hour on a 1920x440 bar LCD.
"""

import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from scenes.base import BaseScene
from scenes import engine


class SunsetScene(BaseScene):
    """Dramatic sunset with setting sun, warm bloom, god rays,
    silhouette clouds with rim lighting, horizon glow, and haze."""

    def __init__(self, w: int, h: int):
        super().__init__(w, h)

        # --- sky gradient: deep blue-purple top -> warm orange-pink mid -> golden horizon ---
        # We build a multi-stop gradient manually for richer color transitions
        self._gradient = self._build_sky_gradient(w, h)

        # --- sun geometry (low, near horizon, partially below) ---
        self._sun_cx = int(w * 0.50)
        self._sun_cy = int(h * 0.88)  # near bottom, partially clipped
        self._sun_rx = 68  # wider than tall (atmospheric refraction flattening)
        self._sun_ry = 55

        # --- pre-render sun sprite (flattened ellipse, orange-red core) ---
        self._sun_sprite = self._build_sun_sprite()

        # --- pre-render full-canvas sun glow/bloom ---
        self._sun_glow = self._build_sun_glow(w, h)

        rng = random.Random(99)

        # --- god ray parameters (8-10 wide warm rays) ---
        self._ray_count = 9

        # --- silhouette cloud layers (3-4 layers with parallax) ---
        self._clouds = []
        cloud_configs = [
            # (y_range, speed_range, count, size_range, alpha_body, rim_alpha)
            # Far layer: higher, slower, smaller
            (0.28, 0.42, 2.0, 5.0, 2, (350, 550), (50, 80), 35, 60),
            # Mid layer: middle, medium speed
            (0.45, 0.62, 4.0, 10.0, 3, (400, 650), (60, 100), 45, 80),
            # Near layer: lower, faster, larger
            (0.65, 0.82, 8.0, 16.0, 2, (500, 800), (70, 120), 55, 100),
        ]

        for y_lo, y_hi, spd_lo, spd_hi, count, (cw_lo, cw_hi), (ch_lo, ch_hi), body_alpha, rim_a in cloud_configs:
            for ci in range(count):
                cloud_w = rng.randint(cw_lo, cw_hi)
                cloud_h = rng.randint(ch_lo, ch_hi)
                seed = rng.randint(0, 999999)
                # Dark silhouette body
                body_color = (15, 10, 20)
                body_sprite = engine.render_cloud_sprite(
                    cloud_w, cloud_h, body_color, alpha=body_alpha, seed=seed)
                # Bright orange-pink rim (same shape, rendered lighter, used as overlay)
                rim_color = (255, 130, 50)
                rim_sprite = self._build_rim_sprite(cloud_w, cloud_h, rim_color, rim_a, seed)
                x_start = rng.uniform(-cloud_w, w + cloud_w)
                self._clouds.append({
                    'body': body_sprite,
                    'rim': rim_sprite,
                    'x_start': x_start,
                    'x': x_start,
                    'y': rng.uniform(h * y_lo, h * y_hi),
                    'vx': rng.uniform(spd_lo, spd_hi) * rng.choice([-1, 1]),
                    'w': cloud_w,
                    'cw': cloud_w,
                    'h': cloud_h,
                    'layer': y_lo,  # for sorting/parallax
                    'phase': rng.uniform(0, math.tau),
                })

        # Sort clouds by layer for back-to-front rendering (done once)
        self._clouds.sort(key=lambda c: c['layer'])

        # --- horizon glow (intense, warm, across entire bottom) ---
        self._horizon_glow = self._build_horizon_glow(w, h)

        # --- atmospheric haze near horizon ---
        self._haze = self._build_haze(w, h)

        # --- ember/dust particles rising from horizon ---
        self._embers = []
        for _ in range(10):
            self._embers.append({
                'x': rng.uniform(0, w),
                'y': rng.uniform(h * 0.6, h),
                'vy': rng.uniform(-0.5, -0.15),
                'vx': rng.uniform(-0.2, 0.3),
                'phase': rng.uniform(0, math.tau),
                'sprite': engine.glow_sprite(3, (255, 140, 40), alpha_peak=100),
            })

        # --- dark silhouette ground strip (jagged treeline/roofline) ---
        self._ground_sil = Image.new('RGBA', (w, 40), (0, 0, 0, 0))
        sil_draw = ImageDraw.Draw(self._ground_sil)
        # Draw a jagged treeline
        points = [(0, 40)]
        x_pos = 0
        while x_pos < w:
            tree_h = rng.randint(8, 30)
            tree_w = rng.randint(15, 50)
            points.append((x_pos, 40 - tree_h))
            points.append((x_pos + tree_w // 2, 40 - tree_h - rng.randint(3, 12)))
            points.append((x_pos + tree_w, 40 - tree_h))
            x_pos += tree_w + rng.randint(5, 20)
        points.append((w, 40))
        points.append((w, 40))
        sil_draw.polygon(points, fill=(8, 5, 15, 230))

    # ------------------------------------------------------------------
    # Pre-computed assets
    # ------------------------------------------------------------------

    def _build_sky_gradient(self, w: int, h: int) -> Image.Image:
        """Multi-stop vertical gradient for dramatic sunset sky."""
        arr = np.zeros((h, w, 4), dtype=np.uint8)
        # Color stops: (fractional y, R, G, B)
        stops = [
            (0.00, 20, 15, 60),    # deep blue-purple at top
            (0.20, 40, 20, 80),    # purple
            (0.40, 100, 40, 70),   # mauve-pink transition
            (0.55, 180, 65, 45),   # warm pink-orange
            (0.70, 210, 90, 35),   # rich orange
            (0.85, 220, 120, 30),  # golden orange at horizon
            (1.00, 200, 100, 25),  # slightly darker at very bottom (ground)
        ]
        for row in range(h):
            frac = row / max(h - 1, 1)
            # Find surrounding stops
            for si in range(len(stops) - 1):
                if frac <= stops[si + 1][0]:
                    f0, r0, g0, b0 = stops[si]
                    f1, r1, g1, b1 = stops[si + 1]
                    t = (frac - f0) / max(f1 - f0, 1e-6)
                    # Smooth interpolation (ease in-out)
                    t = t * t * (3 - 2 * t)
                    r = int(r0 + (r1 - r0) * t)
                    g = int(g0 + (g1 - g0) * t)
                    b = int(b0 + (b1 - b0) * t)
                    arr[row, :, 0] = r
                    arr[row, :, 1] = g
                    arr[row, :, 2] = b
                    arr[row, :, 3] = 255
                    break
        return Image.fromarray(arr, 'RGBA')

    def _build_sun_sprite(self) -> Image.Image:
        """Pre-render the sun disc: flattened ellipse, deep orange-red core."""
        rx, ry = self._sun_rx, self._sun_ry
        pad = 20
        sprite_w = rx * 2 + pad * 2
        sprite_h = ry * 2 + pad * 2
        cx = sprite_w // 2
        cy = sprite_h // 2

        ys = np.arange(sprite_h, dtype=np.float32)
        xs = np.arange(sprite_w, dtype=np.float32)
        yy, xx = np.meshgrid(ys, xs, indexing='ij')

        # Elliptical distance (normalized 0-1 at edge)
        dist = np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2)
        mask = dist < 1.4  # include feathered edge

        arr = np.zeros((sprite_h, sprite_w, 4), dtype=np.float32)
        n = dist[mask]

        # Color: bright golden core -> deep orange edge
        r_ch = np.where(n < 0.3, 255,
               np.where(n < 0.7, 255,
                        255 - (n - 0.7) / 0.7 * 30)).clip(220, 255)
        g_ch = np.where(n < 0.3, 200,
               np.where(n < 0.7, 200 - (n - 0.3) / 0.4 * 60,
                        140 - (n - 0.7) / 0.7 * 50)).clip(60, 200)
        b_ch = np.where(n < 0.3, 60,
               np.where(n < 0.7, 60 - (n - 0.3) / 0.4 * 30,
                        30 - (n - 0.7) / 0.7 * 20)).clip(5, 60)

        # Alpha: solid core, feathered edge
        alpha = np.where(n < 0.75, 255.0,
                np.clip((1.0 - n) / 0.65 * 255, 0, 255))

        arr[mask, 0] = np.clip(r_ch, 0, 255)
        arr[mask, 1] = np.clip(g_ch, 0, 255)
        arr[mask, 2] = np.clip(b_ch, 0, 255)
        arr[mask, 3] = alpha

        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGBA')

    def _build_sun_glow(self, w: int, h: int) -> Image.Image:
        """Pre-render massive warm bloom centered on sun, covering bottom half."""
        scx, scy = self._sun_cx, self._sun_cy
        rx = self._sun_rx

        source = Image.new('RGBA', (w, h), (0, 0, 0, 0))

        # Intense core glow (warm orange)
        core = engine.glow_sprite(rx + 15, (255, 160, 40), alpha_peak=220)
        engine.stamp_glow(source, scx, scy, core)

        # Medium warm halo
        mid = engine.glow_sprite(rx * 3, (255, 120, 30), alpha_peak=100)
        engine.stamp_glow(source, scx, scy, mid)

        # Wide atmospheric orange glow (covers most of bottom)
        wide = engine.glow_sprite(min(rx * 7, h), (255, 100, 20), alpha_peak=55)
        engine.stamp_glow(source, scx, scy, wide)

        # Ultra-wide soft warm wash
        ultra = engine.glow_sprite(min(rx * 10, w // 3), (220, 80, 15), alpha_peak=30)
        engine.stamp_glow(source, scx, scy, ultra)

        # Multi-pass bloom
        result = engine.multi_bloom(
            source,
            passes=[
                (15, 1.4),   # tight intense core
                (50, 0.9),   # medium warm halo
                (120, 0.5),  # wide atmospheric
                (200, 0.25), # ultra-wide ambient
            ],
        )

        # Warm tint shift
        arr = np.array(result, dtype=np.float32)
        arr[..., 0] = np.clip(arr[..., 0] * 1.1, 0, 255)
        arr[..., 2] = np.clip(arr[..., 2] * 0.7, 0, 255)
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGBA')

    def _build_rim_sprite(self, cw: int, ch: int, color: tuple,
                          alpha: int, seed: int) -> Image.Image:
        """Build a rim-lighting sprite for a cloud: bright edges, transparent center.

        Uses the same cloud shape but applies an edge-detection-like effect
        so only the outline glows warmly.
        """
        # Render a full cloud sprite at the same shape
        full = engine.render_cloud_sprite(cw, ch, color, alpha=alpha, seed=seed)

        # Extract alpha channel as density mask
        full_arr = np.array(full, dtype=np.float32)
        density = full_arr[..., 3] / 255.0

        # Edge detect: difference between dilated and original gives rim
        from PIL import ImageFilter as IF
        alpha_img = Image.fromarray((density * 255).astype(np.uint8), 'L')
        # Dilate by max-filtering
        dilated = alpha_img.filter(IF.MaxFilter(7))
        dilated_arr = np.array(dilated, dtype=np.float32) / 255.0

        # Rim = dilated - eroded (approximated as dilated - original * threshold)
        eroded = alpha_img.filter(IF.MinFilter(5))
        eroded_arr = np.array(eroded, dtype=np.float32) / 255.0
        rim_mask = np.clip(dilated_arr - eroded_arr, 0, 1)

        # Also add some glow on the top edges (backlit from above by sun)
        # Vertical bias: top portion gets more rim light
        vert_bias = np.linspace(1.5, 0.3, ch, dtype=np.float32).reshape(-1, 1)
        rim_mask = rim_mask * vert_bias

        # Build rim sprite
        rim_arr = np.zeros((ch, cw, 4), dtype=np.uint8)
        rim_arr[..., 0] = color[0]
        rim_arr[..., 1] = color[1]
        rim_arr[..., 2] = color[2]
        rim_arr[..., 3] = np.clip(rim_mask * alpha * 1.5, 0, 255).astype(np.uint8)

        result = Image.fromarray(rim_arr, 'RGBA')
        result = result.filter(IF.GaussianBlur(radius=2))
        return result

    def _build_horizon_glow(self, w: int, h: int) -> Image.Image:
        """Intense warm glow across bottom: orange -> red -> purple fading up."""
        glow_h = int(h * 0.55)
        arr = np.zeros((h, w, 4), dtype=np.uint8)
        glow_top = h - glow_h

        for row in range(glow_h):
            frac = row / max(glow_h - 1, 1)  # 0=top of glow, 1=bottom
            # Smooth fade-in curve
            intensity = frac ** 1.3

            # Color shifts from purple (top of glow) to orange-red (bottom)
            r = int(120 + 135 * frac)
            g = int(30 + 60 * frac)
            b = int(60 - 40 * frac)
            a = int(intensity * 85)

            y = glow_top + row
            arr[y, :, 0] = r
            arr[y, :, 1] = g
            arr[y, :, 2] = b
            arr[y, :, 3] = a

        # Add concentrated center glow (brighter near sun x-position)
        scx = self._sun_cx
        x_coords = np.arange(w, dtype=np.float32)
        x_falloff = np.exp(-((x_coords - scx) ** 2) / (w * 80.0))
        # Apply horizontal concentration to alpha
        for row in range(glow_top, h):
            frac = (row - glow_top) / max(glow_h - 1, 1)
            boost = (x_falloff * frac ** 0.8 * 60).clip(0, 60).astype(np.uint8)
            arr[row, :, 3] = np.clip(arr[row, :, 3].astype(np.int16) + boost, 0, 255).astype(np.uint8)

        return Image.fromarray(arr, 'RGBA')

    def _build_haze(self, w: int, h: int) -> Image.Image:
        """Subtle warm atmospheric haze near the horizon."""
        haze_h = int(h * 0.30)
        arr = np.zeros((h, w, 4), dtype=np.uint8)
        haze_top = h - haze_h

        for row in range(haze_h):
            frac = row / max(haze_h - 1, 1)
            a = int(frac ** 2.0 * 40)
            y = haze_top + row
            arr[y, :, 0] = 220
            arr[y, :, 1] = 140
            arr[y, :, 2] = 70
            arr[y, :, 3] = a

        return Image.fromarray(arr, 'RGBA')

    # ------------------------------------------------------------------
    # Main render
    # ------------------------------------------------------------------

    def render(self, t: float, weather_data: dict) -> Image.Image:
        w, h = self.w, self.h
        scx, scy = self._sun_cx, self._sun_cy

        # 1. Base sky gradient
        scene = self._gradient.copy()

        # 2. Horizon glow (behind everything)
        scene.alpha_composite(self._horizon_glow)

        # 3. Atmospheric haze
        scene.alpha_composite(self._haze)

        # 4+5. Sun glow + god rays combined onto one additive layer
        # This merges two expensive additive_composite calls into one
        light_layer = self._sun_glow.copy()
        pulse = 0.90 + 0.10 * math.sin(t * 0.25)
        if abs(pulse - 1.0) > 0.01:
            garr = np.array(light_layer)
            garr[..., 3] = np.clip(
                garr[..., 3].astype(np.float32) * pulse, 0, 255
            ).astype(np.uint8)
            light_layer = Image.fromarray(garr, 'RGBA')

        # God rays -- 9 wide, warm, dramatic rays extending upward
        ray_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        ray_draw = ImageDraw.Draw(ray_layer)

        for i in range(self._ray_count):
            # Concentrate rays upward (PI range = upward from horizon)
            base_angle = -math.pi * 0.15 + i * (math.pi * 0.30 / self._ray_count) - math.pi / 2
            # Add some spread outside center
            spread = (i - self._ray_count / 2) * 0.12
            angle = (base_angle + spread
                     + t * 0.02
                     + math.sin(t * 0.25 + i * 1.7) * 0.06)
            pulse_ray = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(t * 0.5 + i * 1.1))

            inner_r = 80  # start beyond the sun edge
            outer_r = max(w, h) * 0.85 * (0.75 + 0.25 * pulse_ray)
            base_alpha = int((22 + 10 * pulse_ray) * pulse_ray)

            num_segs = 6
            for s in range(num_segs):
                frac0 = s / num_segs
                frac1 = (s + 1) / num_segs
                r0 = inner_r + (outer_r - inner_r) * frac0
                r1 = inner_r + (outer_r - inner_r) * frac1

                # Power-curve fadeout
                seg_alpha = int(base_alpha * (1.0 - frac1) ** 1.3)
                if seg_alpha < 1:
                    continue

                # Warm orange-gold color, slightly redder toward outer
                ray_g = int(180 - 40 * frac1)
                ray_b = int(60 - 30 * frac1)

                mx = int(scx + math.cos(angle) * r0)
                my = int(scy + math.sin(angle) * r0)
                ex = int(scx + math.cos(angle) * r1)
                ey = int(scy + math.sin(angle) * r1)

                # Multi-width soft ray: wide dim + medium + bright core
                ray_draw.line([(mx, my), (ex, ey)],
                              fill=(255, ray_g, ray_b, max(1, seg_alpha // 4)), width=9)
                ray_draw.line([(mx, my), (ex, ey)],
                              fill=(255, ray_g, ray_b, max(1, seg_alpha // 2)), width=4)
                ray_draw.line([(mx, my), (ex, ey)],
                              fill=(255, ray_g, ray_b, seg_alpha), width=1)
        # Merge rays into the combined light layer
        light_layer.alpha_composite(ray_layer)

        # Single additive composite for all light effects
        scene = engine.additive_composite(scene, light_layer)

        # 6. Sun disc (flattened ellipse on top of bloom)
        ms = self._sun_sprite
        msw, msh = ms.size
        scene.alpha_composite(ms, dest=(
            scx - msw // 2,
            scy - msh // 2))

        # 7. Silhouette clouds with rim lighting (back to front by layer)
        # Collect all rim sprites onto one layer for a single additive pass
        rim_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        for cloud in self._clouds:
            cw = cloud['cw']
            # Compute position from t directly (no hardcoded dt)
            cx = (cloud['x_start'] + cloud['vx'] * t) % (w + cw + 200) - cw

            # Vertical bob
            cy_int = int(cloud['y'] + math.sin(t * 0.08 + cloud['phase']) * 4)
            cx_int = int(cx)

            # Only draw if visible
            if cx_int + cw > 0 and cx_int < w:
                # Body (dark silhouette)
                scene.alpha_composite(cloud['body'], dest=(cx_int, cy_int))
                # Accumulate rim lighting onto shared layer
                self._paste_sprite(rim_layer, cloud['rim'], cx_int, cy_int)

        # Single additive composite for all rim lighting
        scene = engine.additive_composite(scene, rim_layer)

        # 7b. Ember/dust particles rising from horizon
        for ember in self._embers:
            ember['x'] += ember['vx'] + math.sin(t * 0.3 + ember['phase']) * 0.4
            ember['y'] += ember['vy']
            if ember['y'] < h * 0.3:
                ember['y'] = h * 0.95
                ember['x'] = random.uniform(0, w)
            pulse = 0.5 + 0.5 * math.sin(t * 2 + ember['phase'] * 2)
            if pulse > 0.3:
                engine.stamp_glow(scene, int(ember['x']), int(ember['y']), ember['sprite'])

        # 7c. Dark silhouette ground strip
        scene.alpha_composite(self._ground_sil, dest=(0, h - 40))

        # 8. Color grading: warm orange-gold tint + contrast + vignette (engine PIL-native)
        scene = engine.color_grade(scene, 'sunset')

        return scene

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _paste_sprite(target: Image.Image, sprite: Image.Image,
                      x: int, y: int):
        """Paste sprite onto target at (x, y), handling edge clipping in-place."""
        tw, th = target.size
        sw, sh = sprite.size
        src_x0 = max(0, -x)
        src_y0 = max(0, -y)
        src_x1 = min(sw, tw - x)
        src_y1 = min(sh, th - y)
        dst_x = max(0, x)
        dst_y = max(0, y)
        if src_x1 > src_x0 and src_y1 > src_y0:
            cropped = sprite.crop((src_x0, src_y0, src_x1, src_y1))
            target.alpha_composite(cropped, dest=(dst_x, dst_y))


# --------------------------------------------------------------------------
# Standalone test
# --------------------------------------------------------------------------
if __name__ == '__main__':
    import sys
    import time

    scene = SunsetScene(1920, 440)
    if '--anim' in sys.argv:
        t0 = time.time()
        for i in range(30):
            img = scene.render(i * 0.5, {})
            img.convert('RGB').save(f'/tmp/scene_sunset_{i:03d}.png')
        elapsed = time.time() - t0
        print(f"Saved 30 frames to /tmp/scene_sunset_*.png  ({elapsed:.1f}s)")
    else:
        img = scene.render(1.0, {})
        img.convert('RGB').save('/tmp/scene_sunset.png')
        print("Saved /tmp/scene_sunset.png")
