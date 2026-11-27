"""How a device wants each LED's colour on the wire.

A profile names an encoding for its light guide and for its buttons:

    "encoding": "palette"                                  1 byte, NI palette index
    "encoding": {"type": "rgb", "order": "grb", "max": 127} 3 bytes per LED
    "encoding": {"type": "mono", "max": 127}               1 byte brightness

To support a new format, subclass Encoding and add it to TYPES.
"""

from __future__ import annotations

from typing import Iterable, Union

from . import colors
from .colors import ColorValue, Rgb


class Encoding:
    name = "?"
    bytes_per_led = 1

    def encode(self, value: ColorValue) -> bytes:
        raise NotImplementedError

    def encode_all(self, values: Iterable[ColorValue]) -> bytes:
        return b"".join(self.encode(v) for v in values)

    def describe(self) -> str:
        return self.name


class PaletteEncoding(Encoding):
    """One byte per LED: an index into the NI colour palette (S MK2)."""

    name = "palette"

    def encode(self, value: ColorValue) -> bytes:
        return bytes([colors.to_palette(value) & 0x7F])


class RgbEncoding(Encoding):
    """Three bytes per LED in the given channel order, scaled to 0..max (S MK1)."""

    name = "rgb"
    bytes_per_led = 3

    def __init__(self, order: str = "rgb", max: int = 255):
        order = order.lower()
        if sorted(order) != ["b", "g", "r"]:
            raise ValueError(f"rgb order must be a permutation of 'rgb', got {order!r}")
        if not 1 <= max <= 255:
            raise ValueError(f"rgb max must be 1..255, got {max}")
        self.order = order
        self.max = max

    def encode(self, value: ColorValue) -> bytes:
        r, g, b = colors.preview(value)
        channel = {"r": r, "g": g, "b": b}
        return bytes(round(channel[c] * self.max / 255) for c in self.order)

    def describe(self) -> str:
        return f"rgb ({self.order}, 0-{self.max})"


class MonoEncoding(Encoding):
    """One brightness byte per LED (single-colour LEDs that don't take a palette)."""

    name = "mono"

    def __init__(self, max: int = 127):
        self.max = max

    def encode(self, value: ColorValue) -> bytes:
        if isinstance(value, Rgb):
            fraction = max(value) / 255
        else:
            hue, level = colors.split(value)
            fraction = 0.0 if hue is None else (1.0 if level >= 2 else (level + 1) / 3)
        return bytes([round(fraction * self.max)])

    def describe(self) -> str:
        return f"mono (0-{self.max})"


TYPES = {"palette": PaletteEncoding, "rgb": RgbEncoding, "mono": MonoEncoding}


def from_spec(spec: Union[str, dict, None]) -> Encoding:
    if spec is None:
        spec = "palette"
    if isinstance(spec, str):
        spec = {"type": spec}
    spec = dict(spec)
    kind = spec.pop("type", "palette")
    if kind not in TYPES:
        raise ValueError(f"unknown encoding {kind!r}; choose from: {', '.join(TYPES)}")
    try:
        return TYPES[kind](**spec)
    except TypeError as e:
        raise ValueError(f"bad options for {kind} encoding: {e}") from e
