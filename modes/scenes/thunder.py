"""Thunderstorm weather scene -- dramatic, atmospheric, layered sky.

Heavy parallax storm clouds, branching lightning with multi-frame flash,
dense wind-driven rain with splashes and ripples, wet ground reflections,
dark ground mist, and deep vignette. Designed for 1920x440 bar LCD.
"""

import math
import random
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scenes.base import BaseScene
from scenes import engine


class ThunderScene(BaseScene):

    def __init__(self, w=1920, h=440):
        super().__init__(w, h)

        # Dark stormy gradient -- deep indigo to charcoal
        self._gradient = engine.gradient_fill(w, h, (18, 16, 35), (30, 28, 48))

        rng = random.Random(77)

        # === 3 PARALLAX CLOUD LAYERS ===
        # Far: slow, blurred, dim -- atmospheric depth
        # Mid: medium speed, main storm mass
        # Near: fast, darkest undersides, menacing
        self._cloud_layers = []
        layer_configs = [
            {
                'count': 5, 'speed': (3, 8), 'y': (-40, 60),
                'cw': (500, 800), 'ch': (150, 240),
                'color': (55, 50, 70), 'alpha': 160, 'blur': 3,
            },
            {
                'count': 6, 'speed': (8, 22), 'y': (10, 140),
                'cw': (450, 700), 'ch': (130, 210),
                'color': (45, 42, 62), 'alpha': 185, 'blur': 1,
            },
            {
                'count': 5, 'speed': (16, 38), 'y': (40, 190),
                'cw': (400, 650), 'ch': (110, 180),
                'color': (35, 32, 52), 'alpha': 210, 'blur': 0,
            },
        ]
        for cfg in layer_configs:
            clouds = []
            for _ in range(cfg['count']):
                clouds.append({
                    'x': rng.uniform(-500, w + 500),
                    'y': rng.uniform(*cfg['y']),
                    'cw': rng.uniform(*cfg['cw']),
                    'ch': rng.uniform(*cfg['ch']),
                    'speed': rng.uniform(*cfg['speed']),
                    'seed': rng.randint(0, 99999),
                    'phase': rng.uniform(0, math.tau),
                })
            self._cloud_layers.append((cfg, clouds))

        # === HEAVY RAIN PARTICLES ===
        self._rain = engine.ParticlePool(280)
        for i in range(self._rain.max):
            self._rain.x[i] = rng.uniform(0, w)
            self._rain.y[i] = rng.uniform(-h, h)
            self._rain.vx[i] = rng.uniform(-3.5, -1.5)
            self._rain.vy[i] = rng.uniform(12, 24)
            self._rain.depth[i] = rng.random()
            self._rain.size[i] = 1.0 + self._rain.depth[i] * 2.5
            self._rain.alpha[i] = 70 + self._rain.depth[i] * 130
            self._rain.phase[i] = rng.uniform(0, math.tau)

        # === LIGHTNING STATE ===
        self._bolt_cooldown = 30
        self._bolt_active = 0
        self._bolt_segments: list[list[tuple]] = []
        self._flash_intensity = 0.0

        # === SPLASH AND RIPPLE LISTS ===
        self._splashes: list[dict] = []
        self._ripples: list[dict] = []

        # === GROUND MIST PUFFS ===
        self._mist_puffs = []
        for _ in range(10):
            self._mist_puffs.append({
                'x': rng.uniform(-200, w + 200),
                'y': rng.uniform(h * 0.78, h * 0.96),
                'rx': rng.uniform(100, 300),
                'ry': rng.uniform(12, 35),
                'speed': rng.uniform(5, 18),
                'alpha': rng.randint(18, 40),
                'phase': rng.uniform(0, math.tau),
            })

        # Pre-render cloud sprites + pre-blur at init (avoids per-frame blur)
        for cfg, clouds in self._cloud_layers:
            for cloud in clouds:
                sprite = engine.render_cloud_sprite(
                    int(cloud['cw']), int(cloud['ch']),
                    cfg['color'], cfg['alpha'], cloud['seed'])
                if cfg['blur'] > 0:
                    cloud['_sprite'] = sprite.filter(
                        ImageFilter.GaussianBlur(cfg['blur']))
                else:
                    cloud['_sprite'] = sprite

        # === LIGHT PATCHES — pre-rendered glow sprites (drifting sky glows) ===
        self._light_patches = []
        for _ in range(3):
            rx = int(rng.uniform(140, 300))
            self._light_patches.append({
                'x': rng.uniform(0, w),
                'y': rng.uniform(h * 0.05, h * 0.35),
                'rx': rx,
                'speed': rng.uniform(3, 10),
                'phase': rng.uniform(0, math.tau),
                'sprite': engine.glow_sprite(rx, (80, 90, 140), alpha_peak=12),
            })

        # === GROUND MIST — pre-rendered glow sprites (no per-frame blur) ===
        for puff in self._mist_puffs:
            rx = int(puff['rx'])
            puff['_sprite'] = engine.glow_sprite(rx, (20, 18, 32), alpha_peak=puff['alpha'])

        # === PRE-COMPUTE VIGNETTE as RGBA overlay ===
        x_coords = np.linspace(-1, 1, w, dtype=np.float32)
        y_coords = np.linspace(-1, 1, h, dtype=np.float32)
        X, Y = np.meshgrid(x_coords, y_coords)
        vignette = np.clip(
            1.0 - 0.22 * (X ** 2 * 0.3 + Y ** 2 * 0.7), 0.55, 1.0)
        vig_alpha = ((1.0 - vignette) * 255).astype(np.uint8)
        vig_rgba = np.zeros((h, w, 4), dtype=np.uint8)
        vig_rgba[..., 3] = vig_alpha
        self._vignette_img = Image.fromarray(vig_rgba, 'RGBA')

    def _respawn_drop(self, i, rng_seed=None):
        """Reset rain drop above the screen."""
        self._rain.x[i] = random.uniform(-60, self.w + 60)
        self._rain.y[i] = random.uniform(-self.h * 0.3, -5)
        self._rain.vx[i] = random.uniform(-3.5, -1.5)
        self._rain.vy[i] = random.uniform(12, 24)
        self._rain.depth[i] = random.random()
        self._rain.size[i] = 1.0 + self._rain.depth[i] * 2.5
        self._rain.alpha[i] = 70 + self._rain.depth[i] * 130

    def render(self, t, weather_data):
        w, h = self.w, self.h

        # Frame delta for time-based physics
        if not hasattr(self, '_last_t'):
            self._last_t = t
        dt = min(t - self._last_t, 0.2)  # cap at 200ms to prevent jumps
        self._last_t = t
        dt_scale = dt * 15.0  # normalize to ~1.0 at original 15fps

        # 1. Copy gradient background
        scene = self._gradient.copy()

        # 2. Ambient light -- subtle flickering unease
        flicker = 0.02 * math.sin(t * 0.4) + 0.015 * math.sin(t * 1.7)
        if abs(flicker) > 0.003:
            arr = np.array(scene, dtype=np.float32)
            arr[..., :3] = np.clip(arr[..., :3] * (1.0 + flicker), 0, 255)
            scene = Image.fromarray(arr.astype(np.uint8), 'RGBA')

        # 3. Light patches -- stamp pre-rendered glow sprites (no per-frame blur)
        for lp in self._light_patches:
            lx = (lp['x'] + lp['speed'] * t) % (w + lp['rx'] * 2) - lp['rx']
            ly = lp['y'] + math.sin(t * 0.15 + lp['phase']) * 10
            engine.stamp_glow(scene, int(lx), int(ly), lp['sprite'])

        # 4. Cloud layers (back to front) -- use pre-blurred sprites directly
        for cfg, clouds in self._cloud_layers:
            for cloud in clouds:
                cx = ((cloud['x'] + cloud['speed'] * t)
                      % (w + cloud['cw'] + 200) - cloud['cw'] - 100)
                cy = cloud['y'] + math.sin(t * 0.10 + cloud['phase']) * 7
                sprite = cloud['_sprite']
                px, py = int(cx), int(cy)
                if (px + sprite.size[0] > 0 and px < w
                        and py + sprite.size[1] > 0 and py < h):
                    scene.alpha_composite(sprite, dest=(px, py))

        # 4. Lightning logic
        self._bolt_cooldown -= 1
        if self._bolt_active > 0:
            self._bolt_active -= 1
            self._flash_intensity *= 0.65
        elif self._flash_intensity > 0.01:
            self._flash_intensity *= 0.5

        if self._bolt_cooldown <= 0 and random.random() < 0.05:
            self._bolt_active = 5
            self._flash_intensity = 1.0
            self._bolt_cooldown = random.randint(25, 55)
            margin = w * 0.1
            x_start = random.uniform(margin, w - margin)
            x_end = x_start + random.uniform(-30, 30)
            self._flash_x = int(x_start)
            self._bolt_segments = engine.generate_lightning(
                int(x_start), 10, int(x_end), h - 10)

        # 5. Draw lightning if active
        if self._bolt_active > 0 and self._bolt_segments:
            engine.draw_lightning(scene, self._bolt_segments,
                                 intensity=self._flash_intensity)

        # 6. Flash illumination
        if self._flash_intensity > 0.05:
            flash_alpha = int(40 * self._flash_intensity)
            flash_overlay = Image.new('RGBA', (w, h),
                                      (180, 180, 240, flash_alpha))
            scene.alpha_composite(flash_overlay)
            arr = np.array(scene, dtype=np.float32)
            arr[..., :3] = np.clip(
                arr[..., :3] * (1.0 + 0.2 * self._flash_intensity), 0, 255)
            scene = Image.fromarray(arr.astype(np.uint8), 'RGBA')

            # Ground flash glow under lightning strike
            ground_glow = engine.glow_sprite(
                300, (100, 110, 180),
                alpha_peak=int(40 * self._flash_intensity))
            flash_x = self._flash_x if hasattr(self, '_flash_x') else w // 2
            engine.stamp_glow(scene, flash_x, h - 20, ground_glow)

        # 7. Heavy rain -- 280 drops with gradient-fade streaks
        self._rain.update(dt=max(dt, 0.01) * 8.0)
        rain_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        rain_draw = ImageDraw.Draw(rain_layer)
        indices = self._rain.get_sorted_indices()

        for i in indices:
            x = float(self._rain.x[i])
            y = float(self._rain.y[i])

            if y > h + 10:
                # 30% chance of splash on impact
                if random.random() < 0.30:
                    self._splashes.append({
                        'x': x, 'y': h - random.uniform(5, 25),
                        'vx': random.uniform(-1.5, 1.5),
                        'vy': random.uniform(-3, -1),
                        'gravity': 0.5,
                        'life': 5, 'max_life': 5,
                        'size': random.uniform(1, 3),
                    })
                if random.random() < 0.15:
                    self._ripples.append({
                        'x': x, 'y': h - random.uniform(5, 20),
                        'life': 8, 'max_life': 8,
                        'max_r': random.uniform(3, 9),
                    })
                self._respawn_drop(i)
                continue
            if x < -80 or x > w + 80:
                self._respawn_drop(i)
                continue

            depth = float(self._rain.depth[i])
            length = 8 + depth * 16
            alpha = int(float(self._rain.alpha[i]))
            vx = float(self._rain.vx[i])
            vy = float(self._rain.vy[i])
            speed = max(0.1, math.sqrt(vx * vx + vy * vy))
            nx, ny = vx / speed, vy / speed
            x2 = x - nx * length
            y2 = y - ny * length

            # Storm purples and blues -- darker palette
            r = int(30 + depth * 25)
            g = int(35 + depth * 35)
            b = int(120 + depth * 90)
            w_line = max(1, int(float(self._rain.size[i]) * 0.65))

            # Gradient fade: tail fades out
            mid_x = (x + x2) * 0.5
            mid_y = (y + y2) * 0.5
            tail_alpha = max(10, alpha // 3)
            rain_draw.line([(int(x2), int(y2)), (int(mid_x), int(mid_y))],
                           fill=(r, g, b, tail_alpha), width=w_line)
            rain_draw.line([(int(mid_x), int(mid_y)), (int(x), int(y))],
                           fill=(r, g, b, min(alpha, 255)), width=w_line)

        scene.alpha_composite(rain_layer)

        # 8. Splashes -- gravity arcs
        splash_draw = ImageDraw.Draw(scene)
        remaining = []
        for sp in self._splashes:
            sp['life'] -= dt_scale
            sp['x'] += sp['vx'] * dt_scale
            sp['y'] += sp['vy'] * dt_scale
            sp['vy'] += sp['gravity'] * dt_scale
            if sp['life'] > 0:
                frac = sp['life'] / sp['max_life']
                a = int(120 * frac)
                sz = max(1, int(sp['size'] * frac))
                sx, sy = int(sp['x']), int(sp['y'])
                splash_draw.ellipse(
                    [sx - sz, sy - sz, sx + sz, sy + sz],
                    fill=(140, 150, 195, a))
                remaining.append(sp)
        self._splashes = remaining

        # 9. Ripples -- expanding flat ellipses
        remaining_r = []
        for rp in self._ripples:
            rp['life'] -= dt_scale
            if rp['life'] > 0:
                progress = 1.0 - rp['life'] / rp['max_life']
                r = max(1, int(rp['max_r'] * progress))
                a = int(50 * (1.0 - progress))
                rx, ry = int(rp['x']), int(rp['y'])
                splash_draw.ellipse(
                    [rx - r, ry - r // 3, rx + r, ry + r // 3],
                    outline=(110, 120, 170, a), width=1)
                remaining_r.append(rp)
        self._ripples = remaining_r

        # 10. Wet ground reflection -- flip top portion, darken, low alpha
        reflect_h = 60
        top_strip = scene.crop((0, h - reflect_h - 40, w, h - 40))
        reflected = top_strip.transpose(Image.FLIP_TOP_BOTTOM)
        ref_arr = np.array(reflected, dtype=np.float32)
        ref_arr[..., :3] *= 0.25
        ref_arr[..., 3] = np.clip(ref_arr[..., 3] * 0.18, 0, 255)
        reflected = Image.fromarray(ref_arr.astype(np.uint8), 'RGBA')
        scene.alpha_composite(reflected, dest=(0, h - reflect_h))

        # 11. Ground mist -- stamp pre-rendered glow sprites (no per-frame blur)
        for puff in self._mist_puffs:
            mx = (puff['x'] + puff['speed'] * t) % (w + puff['rx'] * 2) - puff['rx']
            my = puff['y'] + math.sin(t * 0.22 + puff['phase']) * 5
            engine.stamp_glow(scene, int(mx), int(my), puff['_sprite'])

        # 12. Bottom atmosphere -- dark gradient at base
        bottom_h = 55
        bottom_arr = np.zeros((h, w, 4), dtype=np.uint8)
        for y_row in range(h - bottom_h, h):
            frac = (y_row - (h - bottom_h)) / bottom_h
            bottom_arr[y_row, :, 0] = 22
            bottom_arr[y_row, :, 1] = 20
            bottom_arr[y_row, :, 2] = 35
            bottom_arr[y_row, :, 3] = int(frac ** 1.5 * 50)
        scene.alpha_composite(Image.fromarray(bottom_arr, 'RGBA'))

        # 13. Vignette -- pre-rendered RGBA overlay
        scene.alpha_composite(self._vignette_img)

        return scene


if __name__ == '__main__':
    import sys
    scene = ThunderScene(1920, 440)
    if '--anim' in sys.argv:
        for i in range(60):
            img = scene.render(i * 0.067, {})
            img.convert('RGB').save(f'/tmp/scene_thunder_{i:03d}.png')
        print("Saved 60 frames to /tmp/scene_thunder_*.png")
    else:
        # Force a lightning bolt for the static preview
        scene._bolt_active = 4
        scene._flash_intensity = 0.9
        scene._bolt_segments = engine.generate_lightning(960, 10, 975, 430)
        img = scene.render(1.0, {})
        img.convert('RGB').save('/tmp/scene_thunder.png')
        print("Saved /tmp/scene_thunder.png")
