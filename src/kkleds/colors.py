"""Colours.

A colour value is either:

* an ``int`` - an index into the Native Instruments palette, or
* an ``Rgb`` - a true RGB colour (from "#rrggbb" or JSON [r, g, b]).

Devices convert to their own format through an encoding (see encodings.py), so
the same preset works on palette keyboards (S MK2) and RGB ones (S MK1).

The NI palette (as used by the S MK2 light guide and buttons, and Maschine MK3):

    byte = (hue_index << 2) | level        0 = off

hue_index is 1..17 (see HUES, 17 = white). Level, confirmed on an S88 MK2:

    0, 1, 2 = dim, medium, bright
    3       = "pastel": a washed-out, whitish tint of the hue (red:3 looks pink)

Plain colour names default to level 2 (bright, fully saturated).
"""

from __future__ import annotations

import colorsys
from typing import List, NamedTuple, Optional, Sequence, Tuple, Union


class Rgb(NamedTuple):
    r: int
    g: int
    b: int


ColorValue = Union[int, Rgb]

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

# Approximate on-screen equivalents, used for previews, RGB devices and matching.
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
    """Build a palette byte from a hue name (or 1-based hue index) and level 0..3 (3 = pastel)."""
    index = HUES.index(normalize(hue)) + 1 if isinstance(hue, str) else int(hue)
    if not 1 <= index <= len(HUES):
        raise ValueError(f"hue index must be 1..{len(HUES)}, got {index}")
    if not 0 <= brightness <= MAX_BRIGHTNESS:
        raise ValueError(f"brightness must be 0..{MAX_BRIGHTNESS}, got {brightness}")
    return (index << 2) | brightness


def split(value: int) -> Tuple[Optional[str], int]:
    """Return (hue name or None, level) for a palette byte."""
    index = value >> 2
    if index == 0 or index > len(HUES):
        return None, 0
    return HUES[index - 1], value & 0b11


def to_rgb(value: int) -> Tuple[int, int, int]:
    """Approximate RGB for a palette byte."""
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


# --------------------------------------------------------------------------- generic values
def preview(value: ColorValue) -> Tuple[int, int, int]:
    """RGB for any colour value."""
    if isinstance(value, Rgb):
        return (value.r, value.g, value.b)
    return to_rgb(value)


def to_palette(value: ColorValue) -> int:
    """Palette byte for any colour value (RGB snaps to the nearest entry)."""
    if isinstance(value, Rgb):
        return from_rgb(*value)
    return value


def is_off(value: ColorValue) -> bool:
    return preview(value) == (0, 0, 0)


def describe(value: ColorValue) -> str:
    if isinstance(value, Rgb):
        return "#{:02x}{:02x}{:02x}".format(*value)
    hue, brightness = split(value)
    if hue is None:
        return "off"
    return f"{hue}:pastel" if brightness == PASTEL else f"{hue}:{brightness}"


def parse(spec) -> ColorValue:
    """Parse a colour spec.

    Accepts: "off", "red" (= red:2), "red:0".."red:2" (dim..bright),
    "red:pastel" (= red:3), "red:dim/medium/bright", "0x2b" or "43" (raw
    palette byte), an int, "#ff8800" / "#f80" or [r, g, b] (true RGB).
    """
    if isinstance(spec, Rgb):
        return spec
    if isinstance(spec, (list, tuple)):
        if len(spec) != 3 or not all(isinstance(c, int) and 0 <= c <= 255 for c in spec):
            raise ValueError(f"RGB colour must be [r, g, b] with 0-255 values, got {spec!r}")
        return Rgb(*spec)
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
            return Rgb((n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF)
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


def lerp(a: ColorValue, b: ColorValue, t: float) -> ColorValue:
    """Blend two colours along the hue wheel (shortest way round).

    Two palette colours give a palette colour; if either is RGB, the result is RGB.
    """
    ha, sa, va = colorsys.rgb_to_hsv(*(c / 255 for c in preview(a)))
    hb, sb, vb = colorsys.rgb_to_hsv(*(c / 255 for c in preview(b)))
    if is_off(a):
        ha, sa = hb, sb
    if is_off(b):
        hb, sb = ha, sa
    dh = hb - ha
    if dh > 0.5:
        dh -= 1
    elif dh < -0.5:
        dh += 1
    h = (ha + dh * t) % 1.0
    s = sa + (sb - sa) * t
    v = va + (vb - va) * t
    r, g, bl = (round(c * 255) for c in colorsys.hsv_to_rgb(h, s, v))
    if isinstance(a, Rgb) or isinstance(b, Rgb):
        return Rgb(r, g, bl)
    return from_rgb(r, g, bl)


def gradient(stops: Sequence[ColorValue], count: int) -> List[ColorValue]:
    """`count` colours blending evenly through `stops`."""
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
