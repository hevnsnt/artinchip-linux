"""Night sky weather scene -- serene, magical, immersive.

Clear starlit sky with twinkling multi-colored stars, a luminous moon
with crater detail and soft radial moonlight rays, occasional shooting
stars, dark wispy moonlit clouds, subtle horizon glow, and cool-blue
color grading.  Designed to feel peaceful and enchanting on a
1920x440 bar LCD.
"""

import math
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from scenes.base import BaseScene
from scenes import engine


class NightScene(BaseScene):
    """Clear night sky with twinkling stars, glowing moon, shooting stars,
    wispy clouds, and atmospheric horizon glow."""

    def __init__(self, w: int, h: int):
        super().__init__(w, h)

        # --- sky gradient: deep blue top to dark teal-blue horizon ---
        self._gradient = engine.gradient_fill(w, h, (5, 8, 25), (15, 25, 50))

        # --- moon geometry (upper-right area) ---
        self._moon_cx = int(w * 0.78)
        self._moon_cy = int(h * 0.26)
        self._moon_r = 40

        rng = random.Random(42)

        # --- pre-render moon sprite (warm white disc with subtle craters) ---
        self._moon_sprite = self._build_moon_sprite()

        # --- pre-render full-canvas moon glow layers ---
        self._moon_glow = self._build_moon_glow(w, h)

        # --- moonlight ray parameters ---
        self._ray_count = 14

        # --- stars (250 particles, pre-computed) ---
        self._star_count = 250
        self._star_x = np.zeros(self._star_count, dtype=np.float32)
        self._star_y = np.zeros(self._star_count, dtype=np.float32)
        self._star_size = np.zeros(self._star_count, dtype=np.float32)
        self._star_base_alpha = np.zeros(self._star_count, dtype=np.float32)
        self._star_phase = np.zeros(self._star_count, dtype=np.float32)
        self._star_speed = np.zeros(self._star_count, dtype=np.float32)
        self._star_r = np.zeros(self._star_count, dtype=np.uint8)
        self._star_g = np.zeros(self._star_count, dtype=np.uint8)
        self._star_b = np.zeros(self._star_count, dtype=np.uint8)

        # Star color palette: pure white, warm yellow, cool blue
        star_colors = [
            (255, 255, 255),  # pure white
            (255, 255, 255),  # pure white (weighted)
            (255, 248, 220),  # warm cream
            (255, 240, 200),  # warm yellow
            (200, 220, 255),  # cool blue
            (180, 200, 255),  # cooler blue
            (255, 230, 210),  # warm amber
        ]

        moon_cx, moon_cy, moon_r = self._moon_cx, self._moon_cy, self._moon_r
        for i in range(self._star_count):
            # Avoid placing stars on top of the moon + its glow
            while True:
                sx = rng.uniform(0, w)
                sy = rng.uniform(0, h * 0.88)
                dx = sx - moon_cx
                dy = sy - moon_cy
                if math.sqrt(dx * dx + dy * dy) > moon_r * 3.0:
                    break
            self._star_x[i] = sx
            self._star_y[i] = sy
            depth = rng.uniform(0, 1)
            # Deeper stars are smaller and dimmer
            self._star_size[i] = 1 + (1 - depth) * 2  # 1-3
            self._star_base_alpha[i] = 60 + (1 - depth) * 195  # 60-255
            self._star_phase[i] = rng.uniform(0, math.tau)
            self._star_speed[i] = rng.uniform(1.5, 4.0)
            color = rng.choice(star_colors)
            self._star_r[i] = color[0]
            self._star_g[i] = color[1]
            self._star_b[i] = color[2]

        # --- shooting stars state ---
        self._shooters = []
        self._next_shooter_time = rng.uniform(15.0, 45.0)
        self._rng = rng

        # --- dark wispy clouds (2 slow-moving) ---
        self._clouds = []
        for ci in range(2):
            cloud_w = rng.randint(300, 500)
            cloud_h = rng.randint(40, 70)
            seed = rng.randint(0, 999999)
            cloud_color = (30, 35, 50)
            sprite = engine.render_cloud_sprite(
                cloud_w, cloud_h, cloud_color, alpha=40, seed=seed)
            cloud = {
                'sprite': sprite,
                'x': rng.uniform(-200, w),
                'y': rng.uniform(h * 0.18, h * 0.55),
                'vx': rng.uniform(3.0, 8.0),
                'cw': cloud_w,
                'h': cloud_h,
            }
            # For each cloud, create a thin horizontal glow sprite as a "moonlit rim"
            cloud['rim_sprite'] = engine.glow_sprite(
                int(cloud['cw'] // 3), (180, 195, 220), alpha_peak=25)
            self._clouds.append(cloud)

        # --- ground mist (5 drifting elliptical glows near bottom) ---
        self._ground_mist = []
        for _ in range(5):
            rx = int(rng.uniform(120, 220))
            self._ground_mist.append({
                'x': rng.uniform(-100, w + 100),
                'y': h - rng.randint(10, 30),
                'rx': rx,
                'speed': rng.uniform(1.5, 5),
                'phase': rng.uniform(0, math.tau),
                'sprite': engine.glow_sprite(rx, (15, 20, 50), alpha_peak=25),
            })

        # --- fireflies (8 slowly drifting, pulsing green-yellow dots) ---
        self._fireflies = []
        for _ in range(8):
            self._fireflies.append({
                'x': rng.uniform(w * 0.1, w * 0.9),
                'y': rng.uniform(h * 0.5, h * 0.85),
                'vx': rng.uniform(-0.4, 0.4),
                'vy': rng.uniform(-0.3, 0.3),
                'phase': rng.uniform(0, math.tau),
                'sprite': engine.glow_sprite(4, (160, 200, 60), alpha_peak=120),
            })

        # --- horizon glow (subtle warm at very bottom) ---
        glow_h = 90
        glow_arr = np.zeros((h, w, 4), dtype=np.uint8)
        glow_top = h - glow_h
        vert_t = np.linspace(0, 1, glow_h, dtype=np.float32)
        glow_alpha = (vert_t ** 1.5 * 45).astype(np.uint8)
        glow_arr[glow_top:, :, 0] = 25
        glow_arr[glow_top:, :, 1] = 18
        glow_arr[glow_top:, :, 2] = 40
        glow_arr[glow_top:, :, 3] = glow_alpha[:, np.newaxis]
        self._horizon_glow = Image.fromarray(glow_arr, 'RGBA')

        # --- pre-compute vignette ---
        x_lin = np.linspace(-1, 1, w, dtype=np.float32)
        y_lin = np.linspace(-1, 1, h, dtype=np.float32)
        X, Y = np.meshgrid(x_lin, y_lin)
        self._vignette = np.clip(
            1.0 - 0.18 * (X ** 2 * 0.3 + Y ** 2 * 0.7), 0.5, 1.0)

    # ------------------------------------------------------------------
    # Pre-computed assets
    # ------------------------------------------------------------------

    def _build_moon_sprite(self) -> Image.Image:
        """Load photorealistic moon disc from NASA LROC texture."""
        r = self._moon_r
        target_size = r * 2 + 10

        # Load the pre-generated photorealistic moon disc
        moon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 'moon_disc.png')
        try:
            moon = Image.open(moon_path).convert('RGBA')
            moon = moon.resize((target_size, target_size), Image.LANCZOS)
            return moon
        except Exception:
            # Fallback: simple bright disc if image not found
            size = target_size
            cx = cy = size // 2
            arr = np.zeros((size, size, 4), dtype=np.uint8)
            ys, xs = np.mgrid[:size, :size]
            dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2).astype(np.float32)
            mask = dist < r
            arr[mask, 0] = 250
            arr[mask, 1] = 245
            arr[mask, 2] = 235
            edge_fade = np.clip((r - dist) / 2.0, 0, 1)
            arr[..., 3] = (edge_fade * 255 * mask).astype(np.uint8)
            return Image.fromarray(arr, 'RGBA')

    def _build_moon_glow(self, w: int, h: int) -> Image.Image:
        """Pre-render full-canvas moon glow (bloom + atmospheric halo)."""
        mcx, mcy, r = self._moon_cx, self._moon_cy, self._moon_r

        # Build a source with bright moon core on full canvas
        source = Image.new('RGBA', (w, h), (0, 0, 0, 0))

        # Warm white core glow
        core = engine.glow_sprite(r + 5, (255, 250, 240), alpha_peak=240)
        engine.stamp_glow(source, mcx, mcy, core)

        # Medium halo
        mid_halo = engine.glow_sprite(r * 3, (220, 230, 250), alpha_peak=80)
        engine.stamp_glow(source, mcx, mcy, mid_halo)

        # Wide atmospheric glow
        wide_halo = engine.glow_sprite(r * 5, (160, 180, 220), alpha_peak=40)
        engine.stamp_glow(source, mcx, mcy, wide_halo)

        # Multi-pass bloom for beautiful falloff
        result = engine.multi_bloom(
            source,
            passes=[
                (8, 1.5),    # tight core bloom
                (30, 1.0),   # medium blue-white halo
                (80, 0.6),   # wide atmospheric
            ],
        )

        # Tint the bloom slightly blue for night atmosphere
        arr = np.array(result, dtype=np.float32)
        arr[..., 0] *= 0.82
        arr[..., 2] = np.clip(arr[..., 2] * 1.12, 0, 255)
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGBA')

    # ------------------------------------------------------------------
    # Shooting star management
    # ------------------------------------------------------------------

    def _spawn_shooter(self, t: float):
        """Spawn a new shooting star."""
        rng = self._rng
        sx = rng.uniform(self.w * 0.1, self.w * 0.9)
        sy = rng.uniform(0, self.h * 0.35)
        angle = rng.uniform(0.3, 1.0)
        if rng.random() > 0.5:
            angle = math.pi - angle
        speed = rng.uniform(1200, 2000)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed
        self._shooters.append({
            'x': sx, 'y': sy,
            'vx': vx, 'vy': vy,
            'birth': t,
            'life': rng.uniform(0.3, 0.6),
            'length': rng.uniform(60, 120),
            'brightness': rng.randint(180, 255),
        })

    # ------------------------------------------------------------------
    # Main render
    # ------------------------------------------------------------------

    def render(self, t: float, weather_data: dict) -> Image.Image:
        w, h = self.w, self.h

        # 1. Base gradient copy
        scene = self._gradient.copy()

        # 2. Horizon glow
        scene.alpha_composite(self._horizon_glow)

        # 3. Moon glow (pre-computed, with subtle pulse)
        pulse = 0.88 + 0.12 * math.sin(t * 0.3)
        glow = self._moon_glow
        if abs(pulse - 1.0) > 0.01:
            garr = np.array(glow, dtype=np.float32)
            garr[..., 3] = np.clip(garr[..., 3] * pulse, 0, 255)
            glow = Image.fromarray(garr.astype(np.uint8), 'RGBA')
        scene = engine.additive_composite(scene, glow)

        # 4. Moonlight rays -- soft, cool radial rays
        ray_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        ray_draw = ImageDraw.Draw(ray_layer)
        mcx, mcy = self._moon_cx, self._moon_cy
        mr = self._moon_r

        for i in range(self._ray_count):
            base_angle = i * math.tau / self._ray_count
            angle = (base_angle
                     + t * 0.015
                     + math.sin(t * 0.18 + i * 1.7) * 0.04)
            pulse_ray = 0.3 + 0.7 * (0.5 + 0.5 * math.sin(t * 0.4 + i * 0.9))

            inner_r = mr + 10
            outer_r = max(w, h) * 0.50 * (0.7 + 0.3 * pulse_ray)
            base_alpha = int((16 + 8 * pulse_ray) * pulse_ray)

            num_segs = 5
            for s in range(num_segs):
                frac0 = s / num_segs
                frac1 = (s + 1) / num_segs
                r0 = inner_r + (outer_r - inner_r) * frac0
                r1 = inner_r + (outer_r - inner_r) * frac1

                half0 = math.tau / self._ray_count * (0.08 + 0.22 * frac0)
                half1 = math.tau / self._ray_count * (0.08 + 0.22 * frac1)

                seg_alpha = int(base_alpha * (1.0 - frac1) ** 1.8)
                if seg_alpha < 1:
                    continue

                pts = [
                    (mcx + math.cos(angle - half0) * r0,
                     mcy + math.sin(angle - half0) * r0),
                    (mcx + math.cos(angle + half0) * r0,
                     mcy + math.sin(angle + half0) * r0),
                    (mcx + math.cos(angle + half1) * r1,
                     mcy + math.sin(angle + half1) * r1),
                    (mcx + math.cos(angle - half1) * r1,
                     mcy + math.sin(angle - half1) * r1),
                ]
                ray_draw.polygon(
                    [(int(px), int(py)) for px, py in pts],
                    fill=(180, 200, 240, seg_alpha))

        ray_layer = engine.bloom(ray_layer, radius=10, intensity=0.9, downsample=4)
        scene = engine.additive_composite(scene, ray_layer)

        # 5. Moon disc (on top of glow/rays for crisp detail)
        ms = self._moon_sprite
        msw, msh = ms.size
        scene.alpha_composite(ms, dest=(
            self._moon_cx - msw // 2,
            self._moon_cy - msh // 2))

        # 6. Stars -- twinkling with sin-based alpha modulation
        star_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        star_draw = ImageDraw.Draw(star_layer)

        # Vectorized twinkle computation
        twinkle = np.sin(t * self._star_speed + self._star_phase)
        alpha_mod = 0.3 + 0.7 * (twinkle * 0.5 + 0.5)
        alphas = (self._star_base_alpha * alpha_mod).clip(0, 255).astype(int)

        for i in range(self._star_count):
            sx = int(self._star_x[i])
            sy = int(self._star_y[i])
            sz = int(self._star_size[i])
            a = int(alphas[i])
            sr, sg, sb = int(self._star_r[i]), int(self._star_g[i]), int(self._star_b[i])

            if sz <= 1:
                star_draw.point((sx, sy), fill=(sr, sg, sb, a))
            else:
                star_draw.ellipse(
                    [sx - sz, sy - sz, sx + sz, sy + sz],
                    fill=(sr, sg, sb, a))
                # Bright white-hot core for larger bright stars
                if sz >= 2 and a > 160:
                    star_draw.point((sx, sy), fill=(255, 255, 255, min(255, a + 50)))

        # Star cross-glow for brightest stars (4-pointed micro-spikes)
        for i in range(self._star_count):
            if alphas[i] > 200 and self._star_size[i] >= 2:
                sx = int(self._star_x[i])
                sy = int(self._star_y[i])
                a = int(alphas[i]) // 4
                sr, sg, sb = int(self._star_r[i]), int(self._star_g[i]), int(self._star_b[i])
                # Horizontal and vertical micro-spikes
                for d in range(1, 4):
                    sa = max(0, a - d * 15)
                    if sa < 2:
                        break
                    star_draw.point((sx + d, sy), fill=(sr, sg, sb, sa))
                    star_draw.point((sx - d, sy), fill=(sr, sg, sb, sa))
                    star_draw.point((sx, sy + d), fill=(sr, sg, sb, sa))
                    star_draw.point((sx, sy - d), fill=(sr, sg, sb, sa))

        scene.alpha_composite(star_layer)

        # 7. Shooting stars
        if t >= self._next_shooter_time:
            if len(self._shooters) < 2:
                self._spawn_shooter(t)
            self._next_shooter_time = t + self._rng.uniform(30.0, 90.0)

        self._shooters = [s for s in self._shooters if t - s['birth'] < s['life']]

        if self._shooters:
            shoot_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
            shoot_draw = ImageDraw.Draw(shoot_layer)

            for s in self._shooters:
                age = t - s['birth']
                progress = age / s['life']

                hx = s['x'] + s['vx'] * age
                hy = s['y'] + s['vy'] * age

                # Fade envelope
                if progress < 0.15:
                    intensity = progress / 0.15
                elif progress > 0.7:
                    intensity = (1.0 - progress) / 0.3
                else:
                    intensity = 1.0

                trail_len = s['length'] * intensity
                speed_mag = max(1, math.sqrt(s['vx'] ** 2 + s['vy'] ** 2))
                dx_norm = s['vx'] / speed_mag
                dy_norm = s['vy'] / speed_mag
                tail_x = hx - dx_norm * trail_len
                tail_y = hy - dy_norm * trail_len

                brt = int(s['brightness'] * intensity)
                if brt < 5:
                    continue

                # Trail segments with fading alpha
                segments = 8
                for seg in range(segments):
                    f0 = seg / segments
                    f1 = (seg + 1) / segments
                    x0 = tail_x + (hx - tail_x) * f0
                    y0 = tail_y + (hy - tail_y) * f0
                    x1 = tail_x + (hx - tail_x) * f1
                    y1 = tail_y + (hy - tail_y) * f1
                    seg_a = int(brt * (f1 ** 1.5))
                    if seg_a < 2:
                        continue
                    # Outer glow
                    shoot_draw.line(
                        [(int(x0), int(y0)), (int(x1), int(y1))],
                        fill=(130, 170, 255, seg_a // 3), width=5)
                    # Bright body
                    shoot_draw.line(
                        [(int(x0), int(y0)), (int(x1), int(y1))],
                        fill=(210, 225, 255, seg_a), width=2)
                    # White-hot core near head
                    if f1 > 0.6:
                        shoot_draw.line(
                            [(int(x0), int(y0)), (int(x1), int(y1))],
                            fill=(255, 255, 255, min(255, int(seg_a * 1.4))),
                            width=1)

                # Bright head glow
                ihx, ihy = int(hx), int(hy)
                if 0 <= ihx < w and 0 <= ihy < h:
                    head = engine.glow_sprite(
                        5, (200, 220, 255), alpha_peak=int(brt * 0.9))
                    engine.stamp_glow(shoot_layer, ihx, ihy, head)

            shoot_bloom = engine.bloom(
                shoot_layer, radius=6, intensity=1.0, downsample=4)
            scene = engine.additive_composite(scene, shoot_bloom)
            scene.alpha_composite(shoot_layer)

        # 8. Dark wispy clouds drifting slowly
        for cloud in self._clouds:
            cloud['x'] += cloud['vx'] * 0.016
            cw = cloud['cw']
            if cloud['x'] > w + cw:
                cloud['x'] = -cw
            cx_int = int(cloud['x'])
            cy_int = int(cloud['y'])
            if cx_int + cw > 0 and cx_int < w:
                scene.alpha_composite(cloud['sprite'], dest=(cx_int, cy_int))
                # Moonlit rim glow at the top edge of each cloud
                engine.stamp_glow(scene, int(cx_int + cw // 2), int(cy_int - 5), cloud['rim_sprite'])

        # 8b. Ground mist
        for mist in self._ground_mist:
            mx = (mist['x'] + mist['speed'] * t) % (w + mist['rx'] * 2) - mist['rx']
            my = mist['y'] + math.sin(t * 0.12 + mist['phase']) * 4
            engine.stamp_glow(scene, int(mx), int(my), mist['sprite'])

        # 8c. Fireflies
        for ff in self._fireflies:
            ff['x'] += ff['vx'] + math.sin(t * 0.4 + ff['phase']) * 0.5
            ff['y'] += ff['vy'] + math.sin(t * 0.3 + ff['phase'] * 1.3) * 0.3
            if ff['x'] < w * 0.05 or ff['x'] > w * 0.95:
                ff['vx'] *= -1
            if ff['y'] < h * 0.4 or ff['y'] > h * 0.9:
                ff['vy'] *= -1
            # Pulse: fireflies blink on and off
            pulse = max(0, math.sin(t * 1.5 + ff['phase'] * 3))
            if pulse > 0.3:
                engine.stamp_glow(scene, int(ff['x']), int(ff['y']), ff['sprite'])

        # 9. Color grading: cool blue tint, low contrast, vignette
        arr = np.array(scene, dtype=np.float32)

        # Cool blue tint
        tint = np.array([20, 25, 45], dtype=np.float32)
        strength = 0.12
        arr[..., :3] = arr[..., :3] * (1 - strength) + tint * strength * (arr[..., :3] / 255.0)

        # Slight contrast reduction for dreamy feel
        arr[..., :3] = (arr[..., :3] - 127.5) * 0.95 + 127.5

        # Vignette
        arr[..., :3] *= self._vignette[..., np.newaxis]

        scene = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGBA')

        return scene


# --------------------------------------------------------------------------
# Standalone test
# --------------------------------------------------------------------------
if __name__ == '__main__':
    import sys
    import time

    scene = NightScene(1920, 440)
    if '--anim' in sys.argv:
        t0 = time.time()
        for i in range(30):
            img = scene.render(i * 0.5, {'moon_phase': 'Waning Gibbous',
                                          'moon_illumination': '96'})
            img.convert('RGB').save(f'/tmp/scene_night_{i:03d}.png')
        elapsed = time.time() - t0
        print(f"Saved 30 frames to /tmp/scene_night_*.png  ({elapsed:.1f}s)")
    else:
        img = scene.render(1.0, {'moon_phase': 'Waning Gibbous',
                                  'moon_illumination': '96'})
        img.convert('RGB').save('/tmp/scene_night.png')
        print("Saved /tmp/scene_night.png")
