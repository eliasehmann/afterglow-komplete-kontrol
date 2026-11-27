"""Keyboard models and the Frame - the colour of every key on the light guide."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Union

from . import colors, notes, protocol


@dataclass(frozen=True)
class Model:
    key: str
    name: str
    pid: int
    key_count: int
    lowest_note: int  # MIDI note of the leftmost key at the default octave

    @property
    def highest_note(self) -> int:
        return self.lowest_note + self.key_count - 1


S49 = Model("s49", "Komplete Kontrol S49 MK2", 0x1610, 49, 36)
S61 = Model("s61", "Komplete Kontrol S61 MK2", 0x1620, 61, 36)
S88 = Model("s88", "Komplete Kontrol S88 MK2", 0x1630, 88, 21)  # A0..C8

MODELS = {m.key: m for m in (S49, S61, S88)}
MODELS_BY_PID = {m.pid: m for m in MODELS.values()}


class Frame:
    def __init__(self, model: Model, fill: int = colors.OFF):
        self.model = model
        self.keys: List[int] = [fill] * model.key_count

    def copy(self) -> "Frame":
        f = Frame(self.model)
        f.keys = list(self.keys)
        return f

    # -- addressing -----------------------------------------------------
    def index(self, midi: int) -> Optional[int]:
        i = midi - self.model.lowest_note
        return i if 0 <= i < self.model.key_count else None

    def select(self, spec: str = "all") -> List[int]:
        return notes.select(spec, self.model.lowest_note, self.model.highest_note)

    # -- painting -------------------------------------------------------
    def paint(self, midi_notes: Iterable[int], color: int) -> "Frame":
        for n in midi_notes:
            i = self.index(n)
            if i is not None:
                self.keys[i] = color
        return self

    def fill(self, color: int) -> "Frame":
        self.keys = [color] * self.model.key_count
        return self

    def gradient(self, midi_notes: Sequence[int], stops: Sequence[int]) -> "Frame":
        midi_notes = list(midi_notes)
        for n, value in zip(midi_notes, colors.gradient(stops, len(midi_notes))):
            self.paint([n], value)
        return self

    def scale(
        self,
        midi_notes: Iterable[int],
        root: int,
        intervals: Sequence[int],
        color: int,
        root_color: Optional[int] = None,
        other: Optional[int] = None,
    ) -> "Frame":
        pcs = {(root + i) % 12 for i in intervals}
        for n in midi_notes:
            pc = n % 12
            if pc == root % 12 and root_color is not None:
                self.paint([n], root_color)
            elif pc in pcs:
                self.paint([n], color)
            elif other is not None:
                self.paint([n], other)
        return self

    # -- presets --------------------------------------------------------
    def apply_layer(self, layer: dict) -> "Frame":
        if "fill" in layer:
            return self.fill(colors.parse(layer["fill"]))
        sel = self.select(layer.get("keys", "all"))
        if "gradient" in layer:
            return self.gradient(sel, [colors.parse(c) for c in layer["gradient"]])
        if "scale" in layer:
            root, _, kind = str(layer["scale"]).strip().partition(" ")
            return self.scale(
                sel,
                notes.pitch_class(root),
                notes.scale(kind or "major"),
                colors.parse(layer.get("color", "white")),
                colors.parse(layer["root"]) if "root" in layer else None,
                colors.parse(layer["other"]) if "other" in layer else None,
            )
        if "color" in layer:
            return self.paint(sel, colors.parse(layer["color"]))
        raise ValueError(f"preset layer needs fill, color, gradient or scale: {layer}")

    def apply_preset(self, preset: Union[dict, list]) -> "Frame":
        layers = preset["layers"] if isinstance(preset, dict) else preset
        for layer in layers:
            self.apply_layer(layer)
        return self

    @classmethod
    def from_preset_file(cls, model: Model, path: Union[str, Path]) -> "Frame":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(model).apply_preset(data)

    # -- output ---------------------------------------------------------
    def to_report(self) -> bytes:
        return bytes([protocol.LIGHT_GUIDE_REPORT, *(c & protocol.MAX_VALUE for c in self.keys)])
