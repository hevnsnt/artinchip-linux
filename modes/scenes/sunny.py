"""Sunny weather scene -- radiant, golden, immersive.

Smooth radial-gradient sun with white-hot core fading through yellow to
golden orange, animated god rays with power-curve fadeout, anamorphic light
streak, pulsing lens flare, warm horizon glow, swaying grass blades, and
drifting pollen motes.  Designed to feel magical and luminous on a
1920x440 bar LCD.
"""

import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from scenes.base import BaseScene
from scenes import engine


class SunnyScene(BaseScene):
    """Warm sunny day with radial-gradient sun, god rays, bloom, flare,
    anamorphic streak, grass, and pollen."""

    def __init__(self, w: int, h: int):
        super().__init__(w, h)

        # --- sky gradient: vivid blue to warm golden horizon (bright & happy) ---
        self._gradient = engine.gradient_fill(w, h, (40, 80, 180), (210, 170, 70))

        # --- sun geometry ---
        self._sun_cx = int(w * 0.72)
        self._sun_cy = int(h * 0.28)
        self._sun_r = int(min(w, h) * 0.14)
        scx, scy, sr = self._sun_cx, self._sun_cy, self._sun_r

        # --- pre-compute sun distance field (reused every frame) ---
        ys = np.arange(h, dtype=np.float32)
        xs = np.arange(w, dtype=np.float32)
        yy, xx = np.meshgrid(ys, xs, indexing='ij')
        dist = np.sqrt((xx - scx) ** 2 + (yy - scy) ** 2)
        self._sun_norm = dist / max(sr, 1)
        self._sun_mask = self._sun_norm < 1.35  # pixels within feathered edge

        # --- grass blades (~380, every 5px) ---
        rng = random.Random(7)
        self._grass = []
        for bx in range(3, w - 3, 5):
            self._grass.append({
                'x': bx,
                'height': rng.uniform(8, 24),
                'phase': rng.uniform(0, math.tau),
                'shade': rng.uniform(0.7, 1.0),
                'curve': rng.uniform(-0.3, 0.3),
            })

        # --- pollen / golden sparkles (60 particles -- more for a lively feel) ---
        self._pollen = []
        for _ in range(60):
            self._pollen.append({
                'x': rng.uniform(0, w),
                'y': rng.uniform(0, h * 0.85),
                'vx': rng.uniform(-0.4, 0.4),
                'vy': rng.uniform(-0.15, 0.15),
                'size': rng.randint(1, 3),
                'phase': rng.uniform(0, math.tau),
                'alpha': rng.randint(60, 130),
            })

        # --- lens flare elements (6 glows along sun-to-center axis) ---
        self._flare_elements = []
        for _ in range(6):
            self._flare_elements.append({
                't': rng.uniform(0.3, 1.6),
                'radius': rng.randint(6, 25),
                'color': rng.choice([
                    (255, 200, 80), (200, 230, 255), (255, 160, 60),
                    (180, 220, 255), (255, 220, 150),
                ]),
                'alpha': rng.randint(20, 50),
            })

        # --- pre-compute vignette (gentle, 0.10 strength) ---
        x_lin = np.linspace(-1, 1, w, dtype=np.float32)
        y_lin = np.linspace(-1, 1, h, dtype=np.float32)
        X, Y = np.meshgrid(x_lin, y_lin)
        self._vignette = np.clip(
            1.0 - 0.10 * (X ** 2 * 0.3 + Y ** 2 * 0.7), 0.7, 1.0)

        # --- pre-render sun disc sprite (static -- never changes frame to frame) ---
        sun_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        sun_arr = np.array(sun_layer)
        n = self._sun_norm[self._sun_mask]
        r_ch = np.where(n < 0.25, 255,
               np.where(n < 0.55, 255,
               np.where(n < 0.85, (255 - (n - 0.55) / 0.30 * 15).clip(240, 255),
                        235)))
        g_ch = np.where(n < 0.25, 255,
               np.where(n < 0.55, (255 - (n - 0.25) / 0.30 * 30),
               np.where(n < 0.85, (225 - (n - 0.55) / 0.30 * 50),
                        155)))
        b_ch = np.where(n < 0.25, 245,
               np.where(n < 0.55, (245 - (n - 0.25) / 0.30 * 120),
               np.where(n < 0.85, (125 - (n - 0.55) / 0.30 * 85),
                        25)))
        alpha = np.where(n < 0.65, 255.0,
                np.clip(np.maximum(0, (1.0 - n) / 0.70) ** 1.8 * 255, 0, 255))
        sun_arr[self._sun_mask, 0] = np.clip(r_ch, 0, 255).astype(np.uint8)
        sun_arr[self._sun_mask, 1] = np.clip(g_ch, 0, 255).astype(np.uint8)
        sun_arr[self._sun_mask, 2] = np.clip(b_ch, 0, 255).astype(np.uint8)
        sun_arr[self._sun_mask, 3] = alpha.astype(np.uint8)
        self._sun_sprite = Image.fromarray(sun_arr, 'RGBA')

        # --- pre-render anamorphic streak sprite (static horizontal glow) ---
        streak_arr = np.zeros((h, w, 4), dtype=np.uint8)
        x_coords = np.arange(w, dtype=np.float32)
        x_falloff = np.exp(-np.abs(x_coords - scx) / (w * 0.30))
        core_top = max(0, scy - 3)
        core_bot = min(h, scy + 3)
        core_alpha = np.clip(x_falloff * 50, 0, 255).astype(np.uint8)
        for row in range(core_top, core_bot):
            streak_arr[row, :, 0] = 255
            streak_arr[row, :, 1] = 242
            streak_arr[row, :, 2] = 205
            streak_arr[row, :, 3] = core_alpha
        halo_top = max(0, scy - 8)
        halo_bot = min(h, scy + 8)
        halo_alpha = np.clip(x_falloff * 22, 0, 255).astype(np.uint8)
        for row in range(halo_top, halo_bot):
            if core_top <= row < core_bot:
                continue
            streak_arr[row, :, 0] = 255
            streak_arr[row, :, 1] = 218
            streak_arr[row, :, 2] = 155
            streak_arr[row, :, 3] = halo_alpha
        self._streak_sprite = Image.fromarray(streak_arr, 'RGBA')

        # --- wispy cirrus clouds (4 high, thin, bright wisps) ---
        self._wisps = []
        for i in range(4):
            cw = rng.randint(600, 900)
            ch = rng.randint(30, 55)
            self._wisps.append({
                'x': rng.uniform(-200, w),
                'y': rng.uniform(20, 120),
                'cw': cw, 'ch': ch,
                'speed': rng.uniform(3, 8),
                'sprite': engine.render_cloud_sprite(cw, ch, (240, 245, 255), 70, seed=rng.randint(0, 99999)),
            })

        # --- pre-compute warm horizon glow (stronger, happier, bottom 120px) ---
        glow_h = 120
        glow_arr = np.zeros((h, w, 4), dtype=np.uint8)
        glow_top = h - glow_h
        vert_t = np.linspace(0, 1, glow_h, dtype=np.float32)
        glow_alpha = (vert_t ** 1.2 * 110).astype(np.uint8)
        glow_arr[glow_top:, :, 0] = 255
        glow_arr[glow_top:, :, 1] = 200
        glow_arr[glow_top:, :, 2] = 70
        glow_arr[glow_top:, :, 3] = glow_alpha[:, np.newaxis]
        self._horizon_glow = Image.fromarray(glow_arr, 'RGBA')

    # ------------------------------------------------------------------
    # Main render
    # ------------------------------------------------------------------

    def render(self, t: float, weather_data: dict) -> Image.Image:
        w, h = self.w, self.h
        scx, scy, sr = self._sun_cx, self._sun_cy, self._sun_r

        # 1. Base gradient copy
        scene = self._gradient.copy()

        # 2. Sun radial gradient -- pre-rendered sprite composited directly
        sun_layer = self._sun_sprite
        scene.alpha_composite(sun_layer)

        # 3. Sun bloom -- multi-pass bloom on just the sun core
        bloom_layer = engine.multi_bloom(
            sun_layer,
            passes=[(12, 1.2), (35, 0.7), (90, 0.35)],
        )
        scene = engine.additive_composite(scene, bloom_layer)

        # 4. God rays -- 16 rays, each drawn as 5 fading segments
        ray_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        ray_draw = ImageDraw.Draw(ray_layer)

        for i in range(16):
            base_angle = i * math.tau / 16
            angle = (base_angle
                     + t * 0.06
                     + math.sin(t * 0.4 + i * 1.3) * 0.04)
            pulse = 0.5 + 0.5 * math.sin(t * 1.0 + i * 0.9)

            inner_r = sr + 12
            outer_r = max(w, h) * 0.72 * (0.8 + 0.2 * pulse)
            base_alpha = int((18 + 6 * pulse) * pulse)

            num_segs = 5
            for s in range(num_segs):
                frac0 = s / num_segs
                frac1 = (s + 1) / num_segs
                r0 = inner_r + (outer_r - inner_r) * frac0
                r1 = inner_r + (outer_r - inner_r) * frac1

                # Widening half-angle per segment
                half0 = math.tau / 16 * (0.10 + 0.28 * frac0)
                half1 = math.tau / 16 * (0.10 + 0.28 * frac1)

                # Power-curve fadeout
                seg_alpha = int(base_alpha * (1.0 - frac1) ** 1.5)
                if seg_alpha < 1:
                    continue

                pts = [
                    (scx + math.cos(angle - half0) * r0,
                     scy + math.sin(angle - half0) * r0),
                    (scx + math.cos(angle + half0) * r0,
                     scy + math.sin(angle + half0) * r0),
                    (scx + math.cos(angle + half1) * r1,
                     scy + math.sin(angle + half1) * r1),
                    (scx + math.cos(angle - half1) * r1,
                     scy + math.sin(angle - half1) * r1),
                ]
                ray_draw.polygon(
                    [(int(px), int(py)) for px, py in pts],
                    fill=(255, 225, 150, seg_alpha))

        ray_layer = engine.bloom(ray_layer, radius=8, intensity=1.0, downsample=4)
        scene = engine.additive_composite(scene, ray_layer)

        # 5. Anamorphic streak -- pre-rendered sprite composited directly
        scene = engine.additive_composite(scene, self._streak_sprite)

        # 5b. Wispy cirrus clouds drifting across the sky
        for wisp in self._wisps:
            wx = int((wisp['x'] + wisp['speed'] * t) % (w + wisp['cw'] + 100) - wisp['cw'])
            scene.alpha_composite(wisp['sprite'], dest=(wx, int(wisp['y'])))

        # 6. Lens flare -- 6 glow sprites pulsing along sun-to-center axis
        cx_scr, cy_scr = w // 2, h // 2
        ax_dx = cx_scr - scx
        ax_dy = cy_scr - scy
        flare_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))

        for fe in self._flare_elements:
            pulse_f = 0.75 + 0.25 * math.sin(t * 1.5 + fe['t'] * 3.0)
            fx = int(scx + ax_dx * fe['t'])
            fy = int(scy + ax_dy * fe['t'])
            # Fetch sprite at fixed alpha (cached), then scale for pulse
            sprite = engine.glow_sprite(fe['radius'], fe['color'], alpha_peak=fe['alpha'])
            if abs(pulse_f - 1.0) > 0.02:
                arr = np.array(sprite)
                arr[..., 3] = np.clip(arr[..., 3].astype(np.float32) * pulse_f, 0, 255).astype(np.uint8)
                sprite = Image.fromarray(arr, 'RGBA')
            engine.stamp_glow(flare_layer, fx, fy, sprite)

        scene = engine.additive_composite(scene, flare_layer)

        # 7. Warm horizon glow (pre-computed static layer)
        scene.alpha_composite(self._horizon_glow)

        # 8. Grass -- vibrant green ground strip + swaying blades
        grass_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        grass_draw = ImageDraw.Draw(grass_layer)
        ground_y = h - 10
        # Bright green ground
        grass_draw.rectangle([0, ground_y, w, h], fill=(30, 120, 35, 255))
        # Lighter highlight strip on top edge
        grass_draw.rectangle([0, ground_y, w, ground_y + 2], fill=(50, 160, 55, 180))

        for blade in self._grass:
            bx = blade['x']
            sway = math.sin(t * 2 + blade['phase']) * 5
            bh = blade['height']
            shade = blade['shade']
            crv = blade['curve']

            base_x = bx
            base_y = ground_y
            tip_x = bx + sway + crv * bh
            tip_y = ground_y - bh
            mid_x = bx + sway * 0.5 + crv * bh * 0.5
            mid_y = ground_y - bh * 0.5

            # Vibrant greens with golden-green highlights
            r = int(35 * shade)
            g = int(190 * shade)
            b = int(55 * shade)
            col = (r, g, b, 230)

            grass_draw.line(
                [(int(base_x), int(base_y)), (int(mid_x), int(mid_y))],
                fill=col, width=2)
            # Lighter tip catching sunlight
            tip_col = (min(255, r + 30), min(255, g + 30), min(255, b + 15), 200)
            grass_draw.line(
                [(int(mid_x), int(mid_y)), (int(tip_x), int(tip_y))],
                fill=tip_col, width=1)

        scene.alpha_composite(grass_layer)

        # 9. Pollen / dust -- tiny warm dots drifting with sine wobble
        pollen_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        pollen_draw = ImageDraw.Draw(pollen_layer)

        for p in self._pollen:
            p['x'] += p['vx'] + math.sin(t * 0.7 + p['phase']) * 0.2
            p['y'] += p['vy'] + math.cos(t * 0.5 + p['phase']) * 0.15

            if p['x'] < 0:
                p['x'] = w
            elif p['x'] > w:
                p['x'] = 0
            if p['y'] < 0:
                p['y'] = h * 0.85
            elif p['y'] > h * 0.85:
                p['y'] = 0

            px, py = int(p['x']), int(p['y'])
            sz = p['size']
            # Sparkle: some particles flash brighter
            sparkle = 0.6 + 0.4 * math.sin(t * 3.0 + p['phase'] * 2.5)
            a = int(p['alpha'] * sparkle)
            # Golden-white sparkle color
            pollen_draw.ellipse(
                [px - sz, py - sz, px + sz, py + sz],
                fill=(255, 235, 140, a))
            # Tiny bright core for sparkle effect
            if sz >= 2 and sparkle > 0.85:
                pollen_draw.ellipse(
                    [px - 1, py - 1, px + 1, py + 1],
                    fill=(255, 255, 220, min(255, a + 60)))

        scene.alpha_composite(pollen_layer)

        # 10. Vignette (gentle, 0.10 strength -- sunny is bright)
        arr = np.array(scene, dtype=np.float32)
        arr[..., :3] *= self._vignette[..., np.newaxis]
        scene = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGBA')

        return scene


# --------------------------------------------------------------------------
# Standalone test
# --------------------------------------------------------------------------
if __name__ == '__main__':
    import sys
    import time

    scene = SunnyScene(1920, 440)
    if '--anim' in sys.argv:
        t0 = time.time()
        for i in range(30):
            img = scene.render(i * 0.067, {})
            img.convert('RGB').save(f'/tmp/scene_sunny_{i:03d}.png')
        elapsed = time.time() - t0
        print(f"Saved 30 frames to /tmp/scene_sunny_*.png  ({elapsed:.1f}s)")
    else:
        img = scene.render(1.0, {})
        img.convert('RGB').save('/tmp/scene_sunny.png')
        print("Saved /tmp/scene_sunny.png")
