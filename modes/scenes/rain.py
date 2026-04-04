"""Rain weather scene -- dark, immersive, layered storm atmosphere.

Heavy rain with parallax storm clouds, splash particles, puddle ripples,
wet ground reflections, ground mist, diffuse light patches, and a moody
vignette. Matches the atmospheric depth of the overcast scene.
"""

import math
import random
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scenes.base import BaseScene
from scenes import engine


class RainScene(BaseScene):

    def __init__(self, w=1920, h=440):
        super().__init__(w, h)

        # Dark blue-gray gradient -- storm sky
        self._gradient = engine.gradient_fill(w, h, (15, 18, 35), (28, 32, 48))

        rng = random.Random(77)

        # === 3 PARALLAX CLOUD LAYERS (dark storm clouds) ===
        self._cloud_layers = []
        layer_configs = [
            {   # Far -- slow, dim, heavily blurred
                'count': 5, 'speed': (4, 10), 'y': (-30, 70),
                'cw': (420, 720), 'ch': (130, 210),
                'color': (80, 85, 100), 'alpha': 130, 'blur': 3,
            },
            {   # Mid -- moderate speed, main cloud mass
                'count': 6, 'speed': (10, 24), 'y': (10, 150),
                'cw': (350, 620), 'ch': (100, 180),
                'color': (65, 70, 88), 'alpha': 160, 'blur': 1,
            },
            {   # Near -- faster, darkest undersides, sharp
                'count': 5, 'speed': (18, 38), 'y': (50, 190),
                'cw': (300, 560), 'ch': (90, 160),
                'color': (50, 55, 72), 'alpha': 185, 'blur': 0,
            },
        ]
        for cfg in layer_configs:
            clouds = []
            for _ in range(cfg['count']):
                clouds.append({
                    'x': rng.uniform(-400, w + 400),
                    'y': rng.uniform(*cfg['y']),
                    'cw': rng.uniform(*cfg['cw']),
                    'ch': rng.uniform(*cfg['ch']),
                    'speed': rng.uniform(*cfg['speed']),
                    'seed': rng.randint(0, 99999),
                    'phase': rng.uniform(0, math.tau),
                })
            self._cloud_layers.append((cfg, clouds))

        # Pre-render all cloud sprites (cached in engine)
        for cfg, clouds in self._cloud_layers:
            for cloud in clouds:
                engine.render_cloud_sprite(
                    int(cloud['cw']), int(cloud['ch']),
                    cfg['color'], cfg['alpha'], cloud['seed'])

        # === RAIN PARTICLE POOL (220 drops) ===
        self._pool = engine.ParticlePool(220)
        pool = self._pool
        for i in range(220):
            pool.active[i] = True
            pool.x[i] = rng.uniform(0, w + 60)
            pool.y[i] = rng.uniform(-h, h)
            pool.vx[i] = rng.uniform(-3.0, -1.5)
            pool.vy[i] = rng.uniform(9, 20)
            pool.depth[i] = rng.uniform(0, 1)
            pool.size[i] = rng.uniform(0.2, 1.0) + 0.5 * pool.depth[i]
            pool.phase[i] = rng.uniform(0, math.tau)
            pool.life[i] = rng.uniform(0.5, 2.0)  # used as length multiplier

        # === SPLASH + RIPPLE LISTS ===
        self._splashes: list[dict] = []
        self._ripples: list[dict] = []

        # === LIGHT PATCHES (faint, bluish, diffuse through rain) ===
        self._light_patches = []
        for _ in range(3):
            self._light_patches.append({
                'x': rng.uniform(0, w),
                'y': rng.uniform(h * 0.08, h * 0.45),
                'rx': rng.uniform(120, 280),
                'ry': rng.uniform(35, 70),
                'speed': rng.uniform(4, 12),
                'phase': rng.uniform(0, math.tau),
                'intensity': rng.uniform(0.4, 0.8),
            })

        # === GROUND MIST PUFFS (12 breathing puffs) ===
        self._mist_puffs = []
        for _ in range(12):
            self._mist_puffs.append({
                'x': rng.uniform(-200, w + 200),
                'y': rng.uniform(h * 0.78, h * 0.96),
                'rx': rng.uniform(100, 320),
                'ry': rng.uniform(12, 35),
                'speed': rng.uniform(5, 18),
                'alpha': rng.randint(18, 40),
                'phase': rng.uniform(0, math.tau),
            })

        # === PRE-COMPUTE VIGNETTE ===
        x_coords = np.linspace(-1, 1, w, dtype=np.float32)
        y_coords = np.linspace(-1, 1, h, dtype=np.float32)
        X, Y = np.meshgrid(x_coords, y_coords)
        self._vignette = np.clip(
            1.0 - 0.18 * (X ** 2 * 0.3 + Y ** 2 * 0.7), 0.6, 1.0)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _respawn_drop(self, i):
        pool = self._pool
        pool.x[i] = random.uniform(0, self.w + 60)
        pool.y[i] = random.uniform(-self.h, -10)
        pool.vx[i] = random.uniform(-3.0, -1.5)
        pool.vy[i] = random.uniform(9, 20)
        pool.depth[i] = random.uniform(0, 1)
        pool.size[i] = random.uniform(0.2, 1.0) + 0.5 * pool.depth[i]
        pool.life[i] = random.uniform(0.5, 2.0)

    def _maybe_splash(self, x):
        if random.random() > 0.30:
            return
        for _ in range(random.randint(3, 4)):
            self._splashes.append({
                'x': x + random.uniform(-3, 3),
                'y': float(self.h - random.randint(2, 8)),
                'vx': random.uniform(-2.2, 2.2),
                'vy': random.uniform(-4.5, -1.5),
                'gravity': 0.45,
                'life': random.randint(4, 8),
                'max_life': 8,
                'size': random.uniform(0.5, 1.5),
            })

    def _maybe_ripple(self, x):
        if random.random() > 0.20:
            return
        self._ripples.append({
            'x': x,
            'y': float(self.h - random.randint(2, 12)),
            'radius': 1.0,
            'max_radius': random.uniform(8, 20),
            'expand_speed': random.uniform(0.8, 1.6),
            'life': 1.0,
        })

    # ------------------------------------------------------------------
    # Main render
    # ------------------------------------------------------------------

    def render(self, t, weather_data):
        w, h = self.w, self.h
        pool = self._pool

        # 1. Base gradient
        scene = self._gradient.copy()

        # 2. Ambient light shift -- slow subtle brightness pulse
        ambient_shift = 0.025 * math.sin(t * 0.12)
        if abs(ambient_shift) > 0.004:
            arr = np.array(scene, dtype=np.float32)
            arr[..., :3] = np.clip(arr[..., :3] * (1.0 + ambient_shift), 0, 255)
            scene = Image.fromarray(arr.astype(np.uint8), 'RGBA')

        # 3. Light patches -- faint blue-white glows drifting, pulsing
        light_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        light_draw = ImageDraw.Draw(light_layer)
        for lp in self._light_patches:
            lx = (lp['x'] + lp['speed'] * t) % (w + lp['rx'] * 2) - lp['rx']
            ly = lp['y'] + math.sin(t * 0.18 + lp['phase']) * 12
            pulse = 0.4 + 0.6 * math.sin(t * 0.25 + lp['phase'] * 2)
            alpha = int(14 * lp['intensity'] * pulse)
            rx = int(lp['rx'] + math.sin(t * 0.13 + lp['phase']) * 18)
            ry = int(lp['ry'] + math.sin(t * 0.17 + lp['phase']) * 7)
            light_draw.ellipse(
                [int(lx) - rx, int(ly) - ry, int(lx) + rx, int(ly) + ry],
                fill=(110, 120, 150, alpha))
        light_layer = light_layer.filter(ImageFilter.GaussianBlur(6))
        scene = engine.additive_composite(scene, light_layer)

        # 4. Cloud layers back-to-front -- volumetric sprites
        for cfg, clouds in self._cloud_layers:
            cloud_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
            for cloud in clouds:
                cx = ((cloud['x'] + cloud['speed'] * t)
                      % (w + cloud['cw'] + 200) - cloud['cw'] - 100)
                cy = cloud['y'] + math.sin(t * 0.10 + cloud['phase']) * 7
                sprite = engine.render_cloud_sprite(
                    int(cloud['cw']), int(cloud['ch']),
                    cfg['color'], cfg['alpha'], cloud['seed'])
                px, py = int(cx), int(cy)
                if (px + sprite.size[0] > 0 and px < w
                        and py + sprite.size[1] > 0 and py < h):
                    cloud_layer.alpha_composite(sprite, dest=(px, py))
            if cfg['blur'] > 0:
                cloud_layer = cloud_layer.filter(
                    ImageFilter.GaussianBlur(cfg['blur']))
            scene.alpha_composite(cloud_layer)

        # 5. Rain particles -- time-based update for smooth motion
        if not hasattr(self, '_last_t'):
            self._last_t = t
        dt = min(t - self._last_t, 0.2)  # cap at 200ms to prevent jumps
        self._last_t = t
        pool.update(dt=max(dt, 0.01) * 8.0)  # scale to match velocity units
        rain_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        rain_draw = ImageDraw.Draw(rain_layer)

        for i in range(pool.max):
            if pool.y[i] > h or pool.x[i] < -20:
                gx = float(pool.x[i])
                self._maybe_splash(gx)
                self._maybe_ripple(gx)
                self._respawn_drop(i)
                continue

            d = float(pool.depth[i])
            sz = float(pool.size[i])
            len_mult = float(pool.life[i])
            alpha = int(40 + 215 * d * sz)
            length = (8 + 28 * d) * len_mult
            wind = float(pool.vx[i])
            x, y = float(pool.x[i]), float(pool.y[i])

            x2 = x + wind * 0.7
            y2 = y + length

            # Split streak: dim tail (upper 40%) + bright head (lower 60%)
            mid_x = x + (x2 - x) * 0.4
            mid_y = y + (y2 - y) * 0.4

            # Storm blue-purple tones scaled by depth
            tail_c = (50 + int(30 * d), 65 + int(45 * d), 140 + int(60 * d))
            head_c = (90 + int(55 * d), 120 + int(50 * d), 200 + int(40 * d))
            w_px = 2 if sz > 0.9 else 1

            rain_draw.line(
                [(int(x), int(y)), (int(mid_x), int(mid_y))],
                fill=(*tail_c, alpha // 2), width=w_px)
            rain_draw.line(
                [(int(mid_x), int(mid_y)), (int(x2), int(y2))],
                fill=(*head_c, alpha), width=w_px)

        scene.alpha_composite(rain_layer)

        # 6. Splash particles -- tiny arcing ellipses with gravity
        splash_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        splash_draw = ImageDraw.Draw(splash_layer)
        alive_splashes = []
        for sp in self._splashes:
            sp['x'] += sp['vx']
            sp['y'] += sp['vy']
            sp['vy'] += sp['gravity']
            sp['life'] -= 1
            if sp['life'] <= 0:
                continue
            alive_splashes.append(sp)
            frac = sp['life'] / sp['max_life']
            a = int(160 * frac)
            sz = max(1, int(sp['size'] * frac * 2))
            sx, sy = int(sp['x']), int(sp['y'])
            splash_draw.ellipse(
                [sx - sz, sy - sz, sx + sz, sy + sz],
                fill=(140, 160, 210, a))
        self._splashes = alive_splashes
        scene.alpha_composite(splash_layer)

        # 7. Puddle ripple rings -- expanding flat ellipse outlines
        ripple_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        ripple_draw = ImageDraw.Draw(ripple_layer)
        alive_ripples = []
        for rp in self._ripples:
            rp['radius'] += rp['expand_speed']
            rp['life'] = max(0.0, 1.0 - rp['radius'] / rp['max_radius'])
            if rp['life'] <= 0:
                continue
            alive_ripples.append(rp)
            rx = int(rp['radius'])
            ry = max(1, rx // 3)
            a = int(100 * rp['life'])
            cx_r, cy_r = int(rp['x']), int(rp['y'])
            ripple_draw.ellipse(
                [cx_r - rx, cy_r - ry, cx_r + rx, cy_r + ry],
                outline=(90, 120, 185, a), width=1)
        self._ripples = alive_ripples
        scene.alpha_composite(ripple_layer)

        # 8. Wet ground reflection -- flip bottom strip, darken heavily
        strip_h = 35
        strip_top = h - strip_h
        strip = scene.crop((0, strip_top, w, h))
        strip = strip.transpose(Image.FLIP_TOP_BOTTOM)
        strip_arr = np.array(strip, dtype=np.float32)
        strip_arr[..., :3] *= 0.25
        strip_arr[..., 3] = 25
        strip = Image.fromarray(np.clip(strip_arr, 0, 255).astype(np.uint8), 'RGBA')
        refl_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        refl_layer.paste(strip, (0, strip_top))
        scene.alpha_composite(refl_layer)

        # 9. Ground mist -- soft ellipse puffs, breathing alpha, blurred
        mist_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        mist_draw = ImageDraw.Draw(mist_layer)
        for puff in self._mist_puffs:
            mx = (puff['x'] + puff['speed'] * t) % (w + puff['rx'] * 2) - puff['rx']
            my = puff['y'] + math.sin(t * 0.22 + puff['phase']) * 5
            breath = 0.65 + 0.35 * math.sin(t * 0.18 + puff['phase'] * 1.5)
            ma = int(puff['alpha'] * breath)
            engine.draw_soft_ellipse(
                mist_draw, int(mx), int(my),
                int(puff['rx']), int(puff['ry']),
                (55, 62, 82), ma)
        mist_layer = mist_layer.filter(ImageFilter.GaussianBlur(4))
        scene.alpha_composite(mist_layer)

        # 10. Bottom atmosphere -- gradient fade at very bottom
        bottom_h = 45
        bottom_arr = np.zeros((h, w, 4), dtype=np.uint8)
        for y in range(h - bottom_h, h):
            frac = (y - (h - bottom_h)) / bottom_h
            bottom_arr[y, :, 0] = 30
            bottom_arr[y, :, 1] = 35
            bottom_arr[y, :, 2] = 50
            bottom_arr[y, :, 3] = int(frac ** 1.5 * 40)
        scene.alpha_composite(Image.fromarray(bottom_arr, 'RGBA'))

        # 11. Vignette -- pre-computed, 0.18 strength
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

    scene = RainScene(1920, 440)
    if '--anim' in sys.argv:
        t0 = time.time()
        for i in range(30):
            img = scene.render(i * 0.067, {})
            img.convert('RGB').save(f'/tmp/scene_rain_{i:03d}.png')
        elapsed = time.time() - t0
        print(f"Saved 30 frames to /tmp/scene_rain_*.png ({elapsed:.1f}s)")
    else:
        t0 = time.time()
        img = scene.render(1.0, {})
        img.convert('RGB').save('/tmp/scene_rain.png')
        elapsed = time.time() - t0
        print(f"Saved /tmp/scene_rain.png ({elapsed:.2f}s)")
