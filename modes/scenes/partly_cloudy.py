"""Partly cloudy weather scene -- radiant sun, volumetric clouds, god rays.

Bright blue sky with a smooth radial-gradient sun, fading segmented god rays,
volumetric cloud sprites drifting at parallax speeds, silver linings on
sun-facing edges, warm horizon glow, and cinematic vignette. Designed to
feel luminous and airy on a 1920x440 bar LCD.
"""

import math
import random
import numpy as np
from PIL import Image, ImageDraw
from scenes.base import BaseScene
from scenes import engine


class PartlyCloudyScene(BaseScene):

    def __init__(self, w=1920, h=440):
        super().__init__(w, h)

        # --- Sky gradient (rich blue, lighter toward horizon) ---
        self._gradient = engine.gradient_fill(w, h, (30, 55, 130), (80, 110, 170))

        # --- Sun geometry ---
        self.sun_cx = int(w * 0.72)
        self.sun_cy = int(h * 0.28)
        self.sun_r = int(min(w, h) * 0.12)

        # --- Pre-compute sun distance field (numpy, reused every frame) ---
        ys = np.arange(h, dtype=np.float32)
        xs = np.arange(w, dtype=np.float32)
        yy, xx = np.meshgrid(ys, xs, indexing='ij')
        self._sun_dist = np.sqrt((xx - self.sun_cx) ** 2 + (yy - self.sun_cy) ** 2)
        self._sun_norm = self._sun_dist / max(self.sun_r, 1)
        self._sun_mask = self._sun_norm < 1.4  # padded for feathered edge

        # --- pre-render sun disc sprite (static -- never changes frame to frame) ---
        sun_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        sun_arr = np.array(sun_layer)
        norm = self._sun_norm
        mask = self._sun_mask
        r_v = np.where(norm < 0.25, 255,
               np.where(norm < 0.55, 255,
               np.where(norm < 0.85, 255, 245)))
        g_v = np.where(norm < 0.25, 253,
               np.where(norm < 0.55, 242,
               np.where(norm < 0.85, 215, 175)))
        b_v = np.where(norm < 0.25, 242,
               np.where(norm < 0.55, 185,
               np.where(norm < 0.85, 105, 45)))
        alpha = np.zeros_like(norm)
        outer = mask & (norm >= 0.55)
        alpha[mask] = 255
        alpha[outer] = np.clip((1.4 - norm[outer]) / 0.85 * 255, 0, 255)
        sun_arr[mask, 0] = r_v[mask].astype(np.uint8)
        sun_arr[mask, 1] = g_v[mask].astype(np.uint8)
        sun_arr[mask, 2] = b_v[mask].astype(np.uint8)
        sun_arr[mask, 3] = alpha[mask].astype(np.uint8)
        self._sun_sprite = Image.fromarray(sun_arr, 'RGBA')

        # --- 7 cloud configs with varying depth/size/speed ---
        rng = random.Random(77)
        self.clouds = []
        for i in range(7):
            cw_c = rng.uniform(300, 550)
            ch_c = rng.uniform(80, 160)
            depth = i / 6.0
            seed = rng.randint(0, 99999)
            self.clouds.append({
                'x': rng.uniform(-300, w + 300),
                'y': rng.uniform(h * 0.08, h * 0.60),
                'w': cw_c, 'h': ch_c,
                'speed': rng.uniform(8, 35),
                'alpha': rng.randint(160, 225),
                'depth': depth,
                'seed': seed,
            })
        # Pre-render cloud sprites (cached inside engine)
        for cloud in self.clouds:
            shade = int(180 + 50 * cloud['depth'])
            color = (shade, min(255, shade + 8), min(255, shade + 20))
            engine.render_cloud_sprite(
                int(cloud['w']), int(cloud['h']),
                color, cloud['alpha'], cloud['seed'])

        # --- Pre-compute warm horizon glow (static overlay, bottom 80px) ---
        glow_arr = np.zeros((h, w, 4), dtype=np.uint8)
        glow_h = 80
        for y in range(h - glow_h, h):
            frac = (y - (h - glow_h)) / glow_h
            a = int(frac ** 1.5 * 55)
            glow_arr[y, :, 0] = 200
            glow_arr[y, :, 1] = 150
            glow_arr[y, :, 2] = 80
            glow_arr[y, :, 3] = a
        self._horizon_glow = Image.fromarray(glow_arr, 'RGBA')

        # --- Pre-compute vignette (0.10 strength, elliptical) ---
        x_coords = np.linspace(-1, 1, w, dtype=np.float32)
        y_coords = np.linspace(-1, 1, h, dtype=np.float32)
        X, Y = np.meshgrid(x_coords, y_coords)
        self._vignette = np.clip(
            1.0 - 0.10 * (X ** 2 * 0.3 + Y ** 2 * 0.7), 0.55, 1.0)

        # --- ground mist / horizon haze (4 soft glows near bottom) ---
        rng = random.Random(78)
        self._ground_mist = []
        for _ in range(4):
            rx = int(rng.uniform(150, 250))
            self._ground_mist.append({
                'x': rng.uniform(-100, w + 100),
                'y': h - rng.randint(15, 35),
                'rx': rx,
                'speed': rng.uniform(2, 6),
                'phase': rng.uniform(0, math.tau),
                'sprite': engine.glow_sprite(rx, (200, 210, 230), alpha_peak=20),
            })

        # --- pollen / dust particles (25 warm floating specks) ---
        self._pollen = []
        for _ in range(25):
            self._pollen.append({
                'x': rng.uniform(0, w),
                'y': rng.uniform(h * 0.2, h * 0.85),
                'vx': rng.uniform(-0.3, 0.5),
                'vy': rng.uniform(-0.2, 0.2),
                'phase': rng.uniform(0, math.tau),
                'size': rng.uniform(1, 2),
                'alpha': rng.randint(40, 100),
            })

    # ------------------------------------------------------------------

    def render(self, t, weather_data):
        w, h = self.w, self.h
        scx, scy, sr = self.sun_cx, self.sun_cy, self.sun_r

        # 1 --- Sky gradient ---
        scene = self._gradient.copy()

        # 2 --- Sun radial gradient (pre-rendered sprite) ---
        sun_layer = self._sun_sprite

        # 3 --- Sun bloom (additive glow halo) ---
        sun_bloomed = engine.bloom(sun_layer, radius=40, intensity=1.5, downsample=4)
        scene = engine.additive_composite(scene, sun_bloomed)
        scene.alpha_composite(sun_layer)

        # 4 --- Compute shadow_factor from cloud-sun overlap ---
        shadow_factor = 1.0
        cloud_positions = []
        for ci, cloud in enumerate(self.clouds):
            cx = ((cloud['x'] + cloud['speed'] * t)
                  % (w + cloud['w'] + 200)) - cloud['w'] - 100
            cy = cloud['y'] + math.sin(t * 0.12 + ci * 1.7) * 6
            cw_c, ch_c = cloud['w'], cloud['h']
            cloud_positions.append((cx, cy, cw_c, ch_c, ci))
            # Rectangle overlap test with sun center
            if (cx < scx < cx + cw_c) and (cy - ch_c * 0.3 < scy < cy + ch_c * 0.7):
                overlap_x = 1.0 - abs(scx - (cx + cw_c / 2)) / (cw_c / 2)
                overlap_y = 1.0 - abs(scy - cy) / (ch_c * 0.5)
                overlap = max(0.0, min(1.0, overlap_x * overlap_y))
                shadow_factor = min(shadow_factor, 1.0 - overlap * 0.7)
        shadow_factor = max(0.3, shadow_factor)

        # 5 --- God rays (14 rays, 5 fading segments each) ---
        ray_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        ray_draw = ImageDraw.Draw(ray_layer)
        for i in range(14):
            angle = (i / 14) * math.tau + t * 0.025 + math.sin(t * 0.35 + i * 1.3) * 0.02
            pulse = 0.5 + 0.5 * math.sin(t * 0.7 + i * 0.9)
            inner_r = sr + 5
            outer_r = max(w, h) * 0.6 * (0.8 + 0.2 * pulse)
            base_alpha = int((14 + 5 * pulse) * pulse * shadow_factor)

            for s in range(5):
                frac0 = s / 5
                frac1 = (s + 1) / 5
                r0 = inner_r + (outer_r - inner_r) * frac0
                r1 = inner_r + (outer_r - inner_r) * frac1
                half0 = math.tau / 14 * (0.07 + 0.18 * frac0)
                half1 = math.tau / 14 * (0.07 + 0.18 * frac1)
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
                    [(int(x), int(y)) for x, y in pts],
                    fill=(255, 235, 180, seg_alpha))

        ray_layer = engine.bloom(ray_layer, radius=8, intensity=1.0, downsample=4)
        scene = engine.additive_composite(scene, ray_layer)

        # 6 --- Anamorphic streak (subtle horizontal flare through sun) ---
        streak_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        streak_draw = ImageDraw.Draw(streak_layer)
        streak_alpha = int(25 * shadow_factor)
        if streak_alpha > 1:
            sh_half = 4
            streak_draw.rectangle(
                [0, scy - sh_half, w, scy + sh_half],
                fill=(255, 240, 200, streak_alpha))
            streak_layer = engine.bloom(streak_layer, radius=12, intensity=1.0, downsample=4)
            scene = engine.additive_composite(scene, streak_layer)

        # 7 --- Cloud layers (sorted back-to-front by depth) ---
        cloud_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        sorted_clouds = sorted(cloud_positions, key=lambda c: self.clouds[c[4]]['depth'])

        for cx, cy, cw_c, ch_c, ci in sorted_clouds:
            cloud = self.clouds[ci]
            depth = cloud['depth']
            shade = int(180 + 50 * depth)
            cloud_color = (shade, min(255, shade + 8), min(255, shade + 20))

            sprite = engine.render_cloud_sprite(
                int(cw_c), int(ch_c), cloud_color,
                cloud['alpha'], cloud['seed'])

            px, py = int(cx), int(cy)
            if (px + sprite.size[0] > 0 and px < w
                    and py + sprite.size[1] > 0 and py < h):
                cloud_layer.alpha_composite(sprite, dest=(px, py))

        scene.alpha_composite(cloud_layer)

        # 7b --- Pollen / dust particles (warm floating specks) ---
        pollen_draw = ImageDraw.Draw(scene)
        for p in self._pollen:
            p['x'] += p['vx'] + math.sin(t * 0.5 + p['phase']) * 0.3
            p['y'] += p['vy'] + math.sin(t * 0.3 + p['phase'] * 1.5) * 0.2
            if p['x'] > w + 10: p['x'] = -10
            if p['x'] < -10: p['x'] = w + 10
            if p['y'] > h * 0.9: p['y'] = h * 0.2
            if p['y'] < h * 0.15: p['y'] = h * 0.85
            px, py = int(p['x']), int(p['y'])
            sz = int(p['size'])
            pollen_draw.ellipse([px - sz, py - sz, px + sz, py + sz],
                                 fill=(255, 245, 200, p['alpha']))

        # 8 --- Silver linings (bright glow on sun-facing cloud edges) ---
        lining_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lining_layer)
        any_lining = False
        for cx, cy, cw_c, ch_c, ci in sorted_clouds:
            cloud_cx = cx + cw_c / 2
            cloud_cy = cy + ch_c / 2
            dx_s = scx - cloud_cx
            dy_s = scy - cloud_cy
            dist_s = math.hypot(dx_s, dy_s)
            if dist_s < cw_c * 1.5 and dist_s > 10:
                any_lining = True
                depth = self.clouds[ci]['depth']
                edge_angle = math.atan2(dy_s, dx_s)
                lining_alpha = int(
                    max(30, min(80, 100 - dist_s * 0.04))
                    * (0.4 + 0.6 * depth) * shadow_factor)
                # 8-10 bright ellipses along the sun-facing arc
                n_blobs = 8 + int(2 * depth)
                for li in range(n_blobs):
                    center = (n_blobs - 1) / 2
                    la = edge_angle + (li - center) * 0.13
                    lx = int(cloud_cx + math.cos(la) * cw_c * 0.44)
                    ly = int(cloud_cy + math.sin(la) * ch_c * 0.40)
                    lr = int(9 + 7 * (1 - abs(li - center) / (center + 0.1)))
                    ld.ellipse(
                        [lx - lr, ly - lr // 2, lx + lr, ly + lr // 2],
                        fill=(248, 250, 255, lining_alpha))
        if any_lining:
            lining_layer = engine.bloom(lining_layer, radius=6, intensity=1.0, downsample=4)
            scene = engine.additive_composite(scene, lining_layer)

        # 9 --- Shadow overlay (darken when cloud covers sun) ---
        if shadow_factor < 0.95:
            dark_a = int((1.0 - shadow_factor) * 55)
            scene.alpha_composite(Image.new('RGBA', (w, h), (15, 20, 40, dark_a)))

        # 10 --- Warm horizon glow ---
        scene.alpha_composite(self._horizon_glow)

        # 10b --- Ground mist / horizon haze ---
        for mist in self._ground_mist:
            mx = (mist['x'] + mist['speed'] * t) % (w + mist['rx'] * 2) - mist['rx']
            my = mist['y'] + math.sin(t * 0.15 + mist['phase']) * 3
            engine.stamp_glow(scene, int(mx), int(my), mist['sprite'])

        # 11 --- Vignette (0.10 strength, pre-computed) ---
        arr = np.array(scene, dtype=np.float32)
        arr[..., :3] *= self._vignette[..., np.newaxis]
        scene = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGBA')

        return scene


if __name__ == '__main__':
    import sys
    scene = PartlyCloudyScene(1920, 440)
    if '--anim' in sys.argv:
        for i in range(30):
            img = scene.render(i * 0.067, {})
            img.convert('RGB').save(f'/tmp/scene_partly_cloudy_{i:03d}.png')
        print("Saved 30 frames")
    else:
        img = scene.render(1.0, {})
        img.convert('RGB').save('/tmp/scene_partly_cloudy.png')
        print("Saved /tmp/scene_partly_cloudy.png")
