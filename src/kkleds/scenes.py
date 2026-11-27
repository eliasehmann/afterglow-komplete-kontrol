"""Ambient scenes: animated looks for the whole keyboard.

A scene is a continuous colour field over x = 0..1 that changes with time. It's
sampled at every key, every touch strip dot and every colour button, so one
scene drives the whole surface on any device. The field is RGB; palette
devices snap it to the nearest palette colour, which makes flames flicker
nicely.

To add a scene: subclass Scene, implement sample() (and update() if it has
particles), then add it to SCENES.
"""

from __future__ import annotations

import colorsys
import math
import random
from typing import Dict, List, Optional, Sequence, Tuple, Type

from . import colors
from .colors import ColorValue, Rgb
from .profile import Profile

RGBf = Tuple[float, float, float]  # 0..1 per channel
BRIGHTNESS = {"low": 1 / 3, "medium": 2 / 3, "high": 1.0}  # caps the palette level at dim / medium / bright


# --------------------------------------------------------------------------- noise & colour helpers
def _hash(i: int, j: int, seed: int) -> float:
    h = (i * 374761393 + j * 668265263 + seed * 982451653) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 65535.0


def _smooth(t: float) -> float:
    return t * t * (3 - 2 * t)


def noise(x: float, y: float, seed: int = 0) -> float:
    """Smooth 2D value noise in 0..1."""
    xi, yi = math.floor(x), math.floor(y)
    xf, yf = _smooth(x - xi), _smooth(y - yi)
    a, b = _hash(xi, yi, seed), _hash(xi + 1, yi, seed)
    c, d = _hash(xi, yi + 1, seed), _hash(xi + 1, yi + 1, seed)
    return (a + (b - a) * xf) * (1 - yf) + (c + (d - c) * xf) * yf


def fbm(x: float, y: float, seed: int = 0, octaves: int = 3) -> float:
    total, amp, norm = 0.0, 1.0, 0.0
    for o in range(octaves):
        total += noise(x, y, seed + o * 101) * amp
        norm += amp
        amp *= 0.5
        x *= 2.0
        y *= 2.0
    return total / norm


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def rgb(hex_or_tuple) -> RGBf:
    if isinstance(hex_or_tuple, str):
        n = int(hex_or_tuple.lstrip("#"), 16)
        return ((n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255)
    return tuple(c / 255 for c in hex_or_tuple)


def mix(a: RGBf, b: RGBf, t: float) -> RGBf:
    t = clamp(t)
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def scale(c: RGBf, k: float) -> RGBf:
    return (c[0] * k, c[1] * k, c[2] * k)


def add(a: RGBf, b: RGBf) -> RGBf:
    return (min(1, a[0] + b[0]), min(1, a[1] + b[1]), min(1, a[2] + b[2]))


def ramp(stops: Sequence[Tuple[float, str]], t: float) -> RGBf:
    """Piecewise-linear colour ramp; stops are (position 0..1, "#rrggbb")."""
    t = clamp(t)
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if t <= p1:
            return mix(rgb(c0), rgb(c1), (t - p0) / (p1 - p0) if p1 > p0 else 0)
    return rgb(stops[-1][1])


def sprite(x: float, x0: float, width: float) -> float:
    """1 at x0, fading to 0 one `width` away (about one LED)."""
    return max(0.0, 1.0 - abs(x - x0) / width)


BLACK: RGBf = (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------- base class
class Scene:
    id = ""
    name = ""
    description = ""
    swatch: Tuple[str, ...] = ()

    def __init__(self, seed: Optional[int] = None):
        self.rng = random.Random(seed)
        self.seed = self.rng.randrange(1 << 16)
        self.t = 0.0

    def update(self, t: float, dt: float) -> None:
        self.t = t

    def sample(self, x: float, w: float, layer: str) -> RGBf:
        """Colour at position x (0..1). w is the LED spacing; layer is keys/strip/buttons."""
        raise NotImplementedError


class _Particles(Scene):
    """Scenes with short-lived sparks: [x, born, life, colour]."""

    rate = 3.0  # new particles per second

    def __init__(self, seed=None):
        super().__init__(seed)
        self.items: List[list] = []

    def spawn(self, t: float) -> list:
        raise NotImplementedError

    def update(self, t, dt):
        super().update(t, dt)
        expected = self.rate * dt
        while expected > 0:
            if self.rng.random() < min(1.0, expected):
                self.items.append(self.spawn(t))
            expected -= 1
        self.items = [p for p in self.items if t - p[1] < p[2]]

    def particles(self, x: float, w: float) -> RGBf:
        out = BLACK
        for px, born, life, col in self.items:
            s = sprite(x, px, w * 1.2)
            if s:
                age = (self.t - born) / life
                env = math.sin(math.pi * clamp(age)) if age < 1 else 0
                out = add(out, scale(col, s * env))
        return out


# --------------------------------------------------------------------------- scenes
class Fire(Scene):
    id, name = "fire", "Fire"
    description = "Flickering flames"
    swatch = ("#2a0300", "#b31200", "#ff4a00", "#ff8000", "#ffb000")
    # Only fully saturated reds/oranges: pale tips would snap to the palette's whitish pastel.
    RAMP = [(0.0, "#000000"), (0.25, "#500000"), (0.45, "#c00000"), (0.62, "#ff2000"),
            (0.8, "#ff6000"), (0.92, "#ff9000"), (1.0, "#ffb000")]

    def sample(self, x, w, layer):
        t = self.t
        heat = fbm(x * 6.0, t * 0.7, self.seed) * 0.85 + noise(x * 14.0, t * 2.2, self.seed + 7) * 0.3
        heat = clamp((heat - 0.22) * 1.5) ** 1.2
        return ramp(self.RAMP, heat)


class Candle(Scene):
    id, name = "candle", "Candlelight"
    description = "Warm glow that wavers and gusts"
    swatch = ("#3a1200", "#c85a00", "#ff8000", "#ffa000")
    RAMP = [(0, "#000000"), (0.3, "#601400"), (0.55, "#d04000"), (0.75, "#ff7000"), (1, "#ffa000")]

    def sample(self, x, w, layer):
        t = self.t
        gust = fbm(t * 0.35, 1.3, self.seed)            # slow swells of the whole flame
        flick = noise(t * 3.0, 5.1, self.seed + 2)       # quick waver, same everywhere
        local = noise(x * 3 + t * 0.5, t * 1.2, self.seed + 4)  # a little variation along the keys
        return ramp(self.RAMP, 0.2 + 0.45 * gust + 0.2 * flick + 0.25 * local)


class Lava(Scene):
    id, name = "lava", "Lava lamp"
    description = "Slow molten blobs"
    swatch = ("#300000", "#c00000", "#ff3000", "#ff8000")
    # Pure reds and oranges: dark crimson would snap to the palette's pink/magenta.
    RAMP = [(0, "#000000"), (0.3, "#500000"), (0.5, "#c00000"), (0.68, "#ff2000"), (0.85, "#ff6000"), (1, "#ffa000")]

    def sample(self, x, w, layer):
        v = fbm(x * 3.0 - self.t * 0.06, self.t * 0.18, self.seed) * 1.25 - 0.12
        return ramp(self.RAMP, v)


class Ocean(Scene):
    id, name = "ocean", "Ocean"
    description = "Rolling waves and foam"
    swatch = ("#001040", "#0030ff", "#0098ff", "#00ffe0", "#e0ffff")

    def sample(self, x, w, layer):
        t = self.t
        swell = 0.5 + 0.5 * math.sin(2 * math.pi * (x * 1.6 - t * 0.18))
        chop = fbm(x * 6 + t * 0.35, t * 0.3, self.seed)
        v = swell * 0.55 + chop * 0.55
        return ramp([(0, "#000a30"), (0.35, "#0020c0"), (0.6, "#0070ff"), (0.8, "#00d0ff"),
                     (0.92, "#40ffe8"), (1, "#e8ffff")], v)


class Aurora(Scene):
    id, name = "aurora", "Aurora"
    description = "Drifting northern lights"
    swatch = ("#00300a", "#00ff60", "#00ffc0", "#3060ff", "#9a00ff")

    def sample(self, x, w, layer):
        t = self.t
        hue_pos = fbm(x * 2.2 + t * 0.05, t * 0.1, self.seed)
        col = ramp([(0, "#00ff40"), (0.35, "#00ffa0"), (0.55, "#00c0ff"), (0.75, "#6040ff"), (1, "#c000ff")], hue_pos * 1.2)
        curtain = clamp((fbm(x * 5 - t * 0.15, t * 0.2 + 10, self.seed + 1) - 0.3) * 1.8) ** 1.3
        return scale(col, 0.15 + 0.85 * curtain)


class Sunset(Scene):
    id, name = "sunset", "Sunset"
    description = "Warm sky slowly shifting"
    swatch = ("#4a00a0", "#ff0080", "#ff5000", "#ffc000")

    def sample(self, x, w, layer):
        t = self.t
        pos = x * 0.85 + 0.12 * math.sin(t * 0.12) + 0.06 * fbm(x * 3, t * 0.15, self.seed)
        col = ramp([(0, "#5000c0"), (0.3, "#c000a0"), (0.55, "#ff0050"), (0.78, "#ff5a00"), (1, "#ffc000")], pos)
        return scale(col, 0.75 + 0.25 * math.sin(t * 0.4 + x * 2))


class Rainbow(Scene):
    id, name = "rainbow", "Rainbow flow"
    description = "Every colour, gently flowing"
    swatch = ("#ff0000", "#ffff00", "#00ff00", "#00ffff", "#0000ff", "#ff00ff")

    def sample(self, x, w, layer):
        r, g, b = colorsys.hsv_to_rgb((x * 0.9 + self.t * 0.08) % 1.0, 1.0, 1.0)
        return (r, g, b)


class Breathe(Scene):
    id, name = "breathe", "Breathe"
    description = "Slow calming pulse"
    swatch = ("#001040", "#2050ff", "#80c0ff")

    def sample(self, x, w, layer):
        phase = 0.5 - 0.5 * math.cos(2 * math.pi * self.t / 7.0)
        return scale(rgb("#2060ff"), 0.08 + 0.92 * phase)


class NightSky(_Particles):
    id, name = "night", "Night sky"
    description = "Deep blue with twinkling stars"
    swatch = ("#000830", "#001060", "#ffffff", "#001060")
    rate = 5.0

    def spawn(self, t):
        return [self.rng.random(), t, self.rng.uniform(1.2, 3.0), rgb("#ffffff")]

    def sample(self, x, w, layer):
        base = scale(rgb("#0020a0"), 0.32 + 0.1 * fbm(x * 3, self.t * 0.1, self.seed))
        return add(base, self.particles(x, w))


class Forest(_Particles):
    id, name = "forest", "Fireflies"
    description = "Dark forest with drifting fireflies"
    swatch = ("#002000", "#00a020", "#c0ff00", "#002000")
    rate = 3.0

    def spawn(self, t):
        return [self.rng.random(), t, self.rng.uniform(1.5, 3.5), rgb(self.rng.choice(["#d0ff00", "#ffe000", "#80ff00"]))]

    def sample(self, x, w, layer):
        leaves = fbm(x * 4 + self.t * 0.05, self.t * 0.15, self.seed)
        base = scale(ramp([(0, "#004000"), (1, "#00a030")], leaves), 0.35 + 0.15 * leaves)
        return add(base, self.particles(x, w))


class Rain(_Particles):
    id, name = "rain", "Rain"
    description = "Drops rippling across the keys"
    swatch = ("#001030", "#0040ff", "#00c0ff", "#001030")
    rate = 6.0

    def spawn(self, t):
        return [self.rng.random(), t, 0.9, rgb("#40d0ff")]

    def sample(self, x, w, layer):
        out = scale(rgb("#0018a0"), 0.3)
        for px, born, life, col in self.items:
            age = self.t - born
            radius = age * 0.18
            ring = sprite(abs(x - px), radius, w * 1.5)
            centre = sprite(x, px, w) * max(0.0, 1 - age * 4)
            out = add(out, scale(col, (ring * 0.7 + centre) * (1 - age / life)))
        return out


class Storm(Scene):
    id, name = "storm", "Thunderstorm"
    description = "Brooding clouds and lightning"
    swatch = ("#100020", "#301050", "#ffffff", "#200840")

    def __init__(self, seed=None):
        super().__init__(seed)
        self.next_strike = 2.0
        self.strikes: List[Tuple[float, float]] = []  # (time, x)

    def update(self, t, dt):
        super().update(t, dt)
        if t >= self.next_strike:
            x = self.rng.random()
            self.strikes.append((t, x))
            if self.rng.random() < 0.6:  # double flash
                self.strikes.append((t + self.rng.uniform(0.12, 0.25), x))
            self.next_strike = t + self.rng.uniform(2.5, 7.0)
        self.strikes = [(s, x) for s, x in self.strikes if t - s < 0.6]

    def sample(self, x, w, layer):
        cloud = fbm(x * 3 + self.t * 0.08, self.t * 0.2, self.seed)
        out = scale(ramp([(0, "#100030"), (1, "#5020a0")], cloud), 0.25 + 0.25 * cloud)
        for s, sx in self.strikes:
            age = self.t - s
            if age < 0:
                continue
            reach = clamp(1 - abs(x - sx) * 1.4)
            out = add(out, scale(rgb("#e0e8ff"), reach * math.exp(-age / 0.13)))
        return out


class Synthwave(Scene):
    id, name = "synthwave", "Synthwave"
    description = "Neon pulses racing by"
    swatch = ("#20004a", "#ff00c0", "#00e0ff", "#20004a")

    def sample(self, x, w, layer):
        t = self.t
        base = scale(rgb("#a000ff"), 0.35 + 0.15 * math.sin(t * 0.8 + x * 6))
        out = base
        for k, (speed, col) in enumerate(((0.35, "#ff00d0"), (0.22, "#00e8ff"))):
            head = (t * speed + k * 0.5) % 1.4 - 0.2
            d = head - x
            if 0 <= d < 0.25:
                out = add(out, scale(rgb(col), (1 - d / 0.25) ** 2))
        return out


class Matrix(Scene):
    id, name = "matrix", "Code rain"
    description = "Green streaks with bright heads"
    swatch = ("#001000", "#00a000", "#80ff80", "#001000")

    def __init__(self, seed=None):
        super().__init__(seed)
        self.streaks = [[self.rng.random(), self.rng.uniform(0.08, 0.25), self.rng.uniform(0.05, 0.2)] for _ in range(6)]

    def update(self, t, dt):
        super().update(t, dt)
        for s in self.streaks:
            s[0] -= s[1] * dt
            if s[0] < -s[2]:
                s[0], s[1], s[2] = 1 + self.rng.uniform(0, 0.3), self.rng.uniform(0.08, 0.25), self.rng.uniform(0.05, 0.2)

    def sample(self, x, w, layer):
        out = scale(rgb("#00ff00"), 0.08)
        for head, _speed, length in self.streaks:
            d = x - head
            if -w < d < length:
                tail = 1 - max(0.0, d) / length
                col = rgb("#c0ffc0") if d < w else rgb("#00ff20")
                out = add(out, scale(col, tail))
        return out


class Custom(_Particles):
    """Your own colours with a choice of motion."""

    id, name = "custom", "Custom"
    description = "Your colours, your motion"
    MOTIONS = ("still", "flow", "waves", "pulse", "flicker", "twinkle")
    rate = 5.0

    def __init__(self, seed=None, colours: Sequence[RGBf] = (), motion: str = "flow"):
        super().__init__(seed)
        self.configure(colours or [rgb("#ff0000"), rgb("#0000ff")], motion)

    def configure(self, colours: Sequence[RGBf], motion: str) -> None:
        if motion not in self.MOTIONS:
            raise ValueError(f"unknown motion {motion!r}; choose from: {', '.join(self.MOTIONS)}")
        if not colours:
            raise ValueError("a custom scene needs at least one colour")
        self.colours = list(colours)
        self.motion = motion

    def gradient(self, u: float) -> RGBf:
        cols = self.colours
        if len(cols) == 1:
            return cols[0]
        pos = clamp(u) * (len(cols) - 1)
        i = min(int(pos), len(cols) - 2)
        return mix(cols[i], cols[i + 1], pos - i)

    def spawn(self, t):
        return [self.rng.random(), t, self.rng.uniform(1.0, 2.5), self.rng.choice(self.colours)]

    def update(self, t, dt):
        if self.motion == "twinkle":
            super().update(t, dt)
        else:
            Scene.update(self, t, dt)

    def sample(self, x, w, layer):
        t, m = self.t, self.motion
        if m == "still":
            return self.gradient(x)
        if m == "flow":
            u = (x * 0.8 + t * 0.06) % 2.0
            return self.gradient(u if u <= 1 else 2 - u)
        if m == "waves":
            k = 0.3 + 0.7 * (0.5 + 0.5 * math.sin(2 * math.pi * (x * 2 - t * 0.25)))
            return scale(self.gradient(x), k)
        if m == "pulse":
            return scale(self.gradient(x), 0.1 + 0.9 * (0.5 - 0.5 * math.cos(2 * math.pi * t / 5)))
        if m == "flicker":
            return scale(self.gradient(x), clamp(0.3 + 0.8 * fbm(x * 6, t * 0.8, self.seed)))
        return add(scale(self.gradient(x), 0.3), self.particles(x, w))  # twinkle


SCENES: Dict[str, Type[Scene]] = {
    cls.id: cls
    for cls in (Fire, Candle, Lava, Ocean, Aurora, Sunset, NightSky, Forest, Rain, Storm, Synthwave, Matrix, Rainbow, Breathe)
}


# --------------------------------------------------------------------------- rendering onto a device
def make(scene_id: str, seed: Optional[int] = None) -> Scene:
    if scene_id == "custom":
        return Custom(seed)
    if scene_id not in SCENES:
        raise ValueError(f"unknown scene {scene_id!r}; choose from: {', '.join(SCENES)}")
    return SCENES[scene_id](seed)


def custom_colours(specs: Sequence) -> List[RGBf]:
    """Colour specs from the UI ("red", "#ff8800", ...) as scene colours (full brightness)."""
    if not specs:
        raise ValueError("a custom scene needs at least one colour")
    if len(specs) > 8:
        raise ValueError("a custom scene can have at most 8 colours")
    out = []
    for spec in specs:
        value = colors.parse(spec)
        if isinstance(value, int):  # palette colour: use its full-brightness hue
            hue, _level = colors.split(value)
            value = colors.make(hue, 2) if hue else value
        out.append(tuple(c / 255 for c in colors.preview(value)))
    return out


def brightness_factor(value) -> float:
    if isinstance(value, str):
        if value not in BRIGHTNESS:
            raise ValueError(f"brightness must be one of {', '.join(BRIGHTNESS)} or 0..1")
        return BRIGHTNESS[value]
    return clamp(float(value), 0.05, 1.0)


def _to_value(c: RGBf, k: float) -> Rgb:
    return Rgb(*(round(clamp(ch * k) * 255) for ch in c))


# Palette hues around the colour wheel, for blending between neighbours.
_HUE_RING = sorted((angle, name) for name, angle in colors._HUE_ANGLE.items())
_PHI1, _PHI2 = 0.6180339887, 0.7548776662  # low-discrepancy steps: scattered, even dither patterns


def _thresholds(i: int, offset: float) -> Tuple[float, float]:
    return ((i * _PHI1 + offset) % 1.0, (i * _PHI2 + offset * 1.7 + 0.31) % 1.0)


def quantize(c: RGBf, level_threshold: float, hue_threshold: float) -> int:
    """RGB -> palette byte with dithering.

    The palette only has 3 levels per hue (plus off) and 16 hues. A brightness or
    hue between two palette steps lights some LEDs at the lower step and some at
    the upper one, in a scattered pattern given by the thresholds. A fade then
    changes the LEDs one by one instead of all jumping together.
    """
    h, s, v = colorsys.rgb_to_hsv(*(clamp(ch) for ch in c))
    p = v * 3 - 1  # -1 = off, 0/1/2 = dim/medium/bright
    low = math.floor(p)
    level = min(2, low + (1 if p - low > level_threshold else 0))
    if level < 0:
        return colors.OFF
    if s < 0.3:
        return colors.make("white", level)
    hue = _HUE_RING[0][1]
    for k, (a0, n0) in enumerate(_HUE_RING):
        a1, n1 = _HUE_RING[(k + 1) % len(_HUE_RING)]
        span = (a1 - a0) % 1.0
        d = (h - a0) % 1.0
        if d <= span:
            hue = n1 if span and d / span > hue_threshold else n0
            break
    pastel = s < 0.7 and v > 0.6
    return colors.make(hue, colors.PASTEL if pastel else level)


def preview(scene: Scene, leds: int, palette: bool) -> List[ColorValue]:
    """A short row of LEDs for a scene's tile in the UI, rendered like the real keys."""
    out = []
    for i in range(leds):
        c = scene.sample((i + 0.5) / leds, 1.0 / leds, "keys")
        out.append(quantize(c, *_thresholds(i, 0.0)) if palette else _to_value(c, 1.0))
    return out


def render(scene: Scene, profile: Profile, brightness: float = 1.0) -> Tuple[List[ColorValue], Optional[List[ColorValue]]]:
    """Sample the scene for every key, strip dot and colour button of a device.

    Palette devices get dithered palette bytes; RGB devices get exact colours.
    """
    def convert(c: RGBf, i: int, offset: float, palette: bool) -> ColorValue:
        c = scale(c, brightness)
        return quantize(c, *_thresholds(i, offset)) if palette else _to_value(c, 1.0)

    keys: List[ColorValue] = []
    lg = profile.light_guide
    if lg is not None:
        n, pal = lg.keys, lg.encoding.name == "palette"
        keys = [convert(scene.sample((i + 0.5) / n, 1.0 / n, "keys"), i, 0.0, pal) for i in range(n)]
    buttons = None
    section = profile.buttons
    if section is not None:
        pal = section.encoding.name == "palette"
        buttons = [colors.OFF] * section.size
        strip = section.strip
        for k, slot in enumerate(strip):
            c = scene.sample((k + 0.5) / len(strip), 1.0 / len(strip), "strip")
            buttons[slot] = convert(c, k, 0.37, pal)
        colour_buttons = sorted((section.color_slots or frozenset()) - set(strip))
        for k, slot in enumerate(colour_buttons):
            c = scene.sample((k + 0.5) / len(colour_buttons), 1.0 / len(colour_buttons), "buttons")
            buttons[slot] = convert(c, k, 0.71, pal)
    return keys, buttons
