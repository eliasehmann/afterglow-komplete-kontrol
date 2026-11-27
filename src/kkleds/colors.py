"""The Native Instruments indexed colour palette used by the light guide.

The MK2 light guide does not take RGB. Each key gets one byte:

    byte = (hue_index << 2) | brightness

where hue_index is 1..17 (see HUES, 17 = white) and 0 means off.
The low two bits (confirmed on an S88 MK2):

    0, 1, 2 = dim, medium, bright
    3       = "pastel": a washed-out, whitish tint of the hue (red:3 looks pink)

Plain colour names default to level 2 (bright, fully saturated).
"""

from __future__ import annotations

import colorsys
from typing import Optional, Tuple, Union

HUES: Tuple[str, ...] = (
    "red",
    "orange",
    "light_orange",
    "warm_yellow",
    "yellow",
    "lime",
    "green",
    "mint",
    "cyan",
    "turquoise",
    "blue",
    "plum",
    "violet",
    "purple",
    "magenta",
    "fuchsia",
    "white",
)

# Approximate on-screen equivalents, used for previews and RGB matching.
HUE_RGB = {
    "red": (255, 0, 0),
    "orange": (255, 70, 0),
    "light_orange": (255, 128, 0),
    "warm_yellow": (255, 190, 0),
    "yellow": (255, 255, 0),
    "lime": (150, 255, 0),
    "green": (0, 255, 0),
    "mint": (0, 255, 140),
    "cyan": (0, 255, 255),
    "turquoise": (0, 150, 255),
    "blue": (0, 0, 255),
    "plum": (90, 0, 255),
    "violet": (150, 0, 255),
    "purple": (210, 0, 255),
    "magenta": (255, 0, 200),
    "fuchsia": (255, 0, 100),
    "white": (255, 255, 255),
}

ALIASES = {
    "pink": "fuchsia",
    "teal": "turquoise",
    "amber": "warm_yellow",
    "gold": "warm_yellow",
    "indigo": "plum",
    "aqua": "cyan",
    "lightorange": "light_orange",
    "warmyellow": "warm_yellow",
}

OFF = 0
MAX_BRIGHTNESS = 3      # highest valid level value (the pastel tint)
DEFAULT_BRIGHTNESS = 2  # brightest fully saturated level
PASTEL = 3
LEVEL_NAMES = {"dim": 0, "low": 0, "medium": 1, "mid": 1, "bright": 2, "high": 2, "pastel": 3, "tint": 3}
RAINBOW = HUES[:-1]  # the 16 chromatic hues, without white

_HUE_ANGLE = {
    name: colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[0]
    for name, rgb in HUE_RGB.items()
    if name != "white"
}

Color = Union[str, int]


def normalize(name: str) -> str:
    key = name.strip().lower().replace("-", "_").replace(" ", "_")
    key = ALIASES.get(key.replace("_", ""), ALIASES.get(key, key))
    if key not in HUES:
        raise ValueError(f"unknown colour {name!r}; choose from: off, {', '.join(HUES)}")
    return key


def make(hue: Union[str, int], brightness: int = DEFAULT_BRIGHTNESS) -> int:
    """Build a colour byte from a hue name (or 1-based hue index) and level 0..3 (3 = pastel)."""
    index = HUES.index(normalize(hue)) + 1 if isinstance(hue, str) else int(hue)
    if not 1 <= index <= len(HUES):
        raise ValueError(f"hue index must be 1..{len(HUES)}, got {index}")
    if not 0 <= brightness <= MAX_BRIGHTNESS:
        raise ValueError(f"brightness must be 0..{MAX_BRIGHTNESS}, got {brightness}")
    return (index << 2) | brightness


def split(value: int) -> Tuple[Optional[str], int]:
    """Return (hue name or None, brightness) for a colour byte."""
    index = value >> 2
    if index == 0 or index > len(HUES):
        return None, 0
    return HUES[index - 1], value & 0b11


def describe(value: int) -> str:
    hue, brightness = split(value)
    if hue is None:
        return "off"
    return f"{hue}:pastel" if brightness == PASTEL else f"{hue}:{brightness}"


def to_rgb(value: int) -> Tuple[int, int, int]:
    """Approximate RGB for a colour byte (for previews)."""
    hue, brightness = split(value)
    if hue is None:
        return (0, 0, 0)
    r, g, b = HUE_RGB[hue]
    if brightness == PASTEL:
        return (round(r / 2 + 127.5), round(g / 2 + 127.5), round(b / 2 + 127.5))
    factor = (brightness + 1) / 3
    return (round(r * factor), round(g * factor), round(b * factor))


def from_rgb(r: int, g: int, b: int) -> int:
    """Nearest palette entry for an RGB colour."""
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    level = round(v * 3) - 1
    if level < 0:
        return OFF
    level = min(level, DEFAULT_BRIGHTNESS)
    if s < 0.3:
        return make("white", level)
    if s < 0.7 and v > 0.6:
        level = PASTEL

    def distance(name: str) -> float:
        d = abs(_HUE_ANGLE[name] - h)
        return min(d, 1 - d)

    return make(min(_HUE_ANGLE, key=distance), level)


def parse(spec: Color) -> int:
    """Parse a colour spec into a byte.

    Accepts: "off", "red" (= red:2), "red:0".."red:2" (dim..bright),
    "red:pastel" (= red:3), "red:dim/medium/bright", "#ff8800", "#f80",
    "0x2b" (raw byte), "43" (raw byte) or an int.
    """
    if isinstance(spec, int):
        value = spec
    else:
        s = spec.strip().lower()
        if s in ("off", "none", "black"):
            return OFF
        if s.startswith("#"):
            digits = s[1:]
            if len(digits) == 3:
                digits = "".join(c * 2 for c in digits)
            if len(digits) != 6:
                raise ValueError(f"bad hex colour {spec!r}")
            n = int(digits, 16)
            return from_rgb((n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF)
        if s.startswith("0x"):
            value = int(s, 16)
        elif s.isdigit():
            value = int(s)
        else:
            name, _, level = s.partition(":")
            if not level:
                return make(name)
            if level in LEVEL_NAMES:
                return make(name, LEVEL_NAMES[level])
            if not level.isdigit():
                raise ValueError(f"bad level {level!r} in {spec!r}; use 0-2, pastel, dim, medium or bright")
            return make(name, int(level))
    if not 0 <= value <= 0x7F:
        raise ValueError(f"raw colour byte must be 0..127, got {value}")
    return value


def gradient(stops, count: int):
    """`count` colour bytes blending evenly through `stops`."""
    stops = list(stops)
    if not stops:
        raise ValueError("gradient needs at least one colour")
    if len(stops) == 1 or count < 2:
        return [stops[0]] * count
    segments = len(stops) - 1
    out = []
    for pos in range(count):
        t = pos / (count - 1) * segments
        seg = min(int(t), segments - 1)
        out.append(lerp(stops[seg], stops[seg + 1], t - seg))
    return out


def lerp(a: int, b: int, t: float) -> int:
    """Blend two colour bytes along the hue wheel (shortest way round)."""
    ha, sa, va = colorsys.rgb_to_hsv(*(c / 255 for c in to_rgb(a)))
    hb, sb, vb = colorsys.rgb_to_hsv(*(c / 255 for c in to_rgb(b)))
    if a == OFF:
        ha, sa = hb, sb
    if b == OFF:
        hb, sb = ha, sa
    dh = hb - ha
    if dh > 0.5:
        dh -= 1
    elif dh < -0.5:
        dh += 1
    h = (ha + dh * t) % 1.0
    s = sa + (sb - sa) * t
    v = va + (vb - va) * t
    r, g, bl = colorsys.hsv_to_rgb(h, s, v)
    return from_rgb(round(r * 255), round(g * 255), round(bl * 255))
