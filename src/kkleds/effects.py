"""Animated effects. Each effect is a generator yielding one key list per frame."""

from __future__ import annotations

import math
import random
from typing import Iterator, List, Optional

from . import colors
from .layout import Model

Frames = Iterator[List[int]]


def _rainbow_at(i: int, n: int, offset: float, cycles: float) -> int:
    hues = colors.RAINBOW
    return colors.make(hues[int(i / n * len(hues) * cycles + offset) % len(hues)])


def rainbow(model: Model, fps: float, speed: float = 1.0, cycles: float = 1.0, **_) -> Frames:
    """A rainbow across the keyboard that scrolls (speed=0 keeps it still)."""
    n = model.key_count
    t = 0
    while True:
        offset = t / fps * speed * 4  # hues per second
        yield [_rainbow_at(i, n, offset, cycles) for i in range(n)]
        t += 1


def chase(model: Model, fps: float, speed: float = 1.0, color: Optional[int] = None, width: int = 4, **_) -> Frames:
    """A bright dot with a fading tail running up and down the keys."""
    n = model.key_count
    hue, _b = colors.split(color or colors.make("cyan"))
    hue = hue or "cyan"
    span = 2 * (n - 1)
    t = 0
    while True:
        pos = int(t / fps * speed * 40) % span
        head = pos if pos < n else span - pos
        direction = 1 if pos < n else -1
        keys = [colors.OFF] * n
        for k in range(width):
            i = head - k * direction
            if 0 <= i < n:
                if colors.DEFAULT_BRIGHTNESS - k >= 0:
                    keys[i] = colors.make(hue, colors.DEFAULT_BRIGHTNESS - k)
        yield keys
        t += 1


def breathe(model: Model, fps: float, speed: float = 1.0, color: Optional[int] = None, **_) -> Frames:
    """The whole keyboard pulses (the hardware only has 3 brightness steps + off)."""
    hue, _b = colors.split(color or colors.make("blue"))
    hue = hue or "blue"
    t = 0
    while True:
        phase = (math.sin(t / fps * speed * math.pi) + 1) / 2  # 0..1
        level = round(phase * 3) - 1
        value = colors.OFF if level < 0 else colors.make(hue, level)
        yield [value] * model.key_count
        t += 1


def sparkle(model: Model, fps: float, speed: float = 1.0, color: Optional[int] = None, density: float = 0.3, **_) -> Frames:
    """Random keys flash and fade out. With --color, all sparks use that hue.

    density = average sparks per key per second (at speed 1).
    """
    n = model.key_count
    fixed_hue = colors.split(color)[0] if color else None
    level = [-1] * n
    hue = ["white"] * n
    rng = random.Random()
    decay_every = max(1, round(fps / (8 * speed)))
    chance = density * speed / fps
    t = 0
    while True:
        if t % decay_every == 0:
            level = [max(-1, lv - 1) for lv in level]
        for i in range(n):
            if rng.random() < chance:
                level[i] = colors.DEFAULT_BRIGHTNESS
                hue[i] = fixed_hue or rng.choice(colors.RAINBOW)
        yield [colors.OFF if lv < 0 else colors.make(hue[i], lv) for i, lv in enumerate(level)]
        t += 1


EFFECTS = {
    "rainbow": rainbow,
    "chase": chase,
    "breathe": breathe,
    "sparkle": sparkle,
}
