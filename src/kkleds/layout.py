"""The Frame - the colour of every key on a light guide."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Union

from . import colors, notes
from .colors import ColorValue
from .profile import Profile


class Frame:
    def __init__(self, profile: Profile, fill: ColorValue = colors.OFF):
        lg = profile.require_light_guide()
        self.profile = profile
        self.keys: List[ColorValue] = [fill] * lg.keys

    @property
    def model(self) -> Profile:  # older name
        return self.profile

    def copy(self) -> "Frame":
        f = Frame(self.profile)
        f.keys = list(self.keys)
        return f

    # -- addressing -----------------------------------------------------
    def index(self, midi: int) -> Optional[int]:
        i = midi - self.profile.lowest_note
        return i if 0 <= i < len(self.keys) else None

    def select(self, spec: str = "all") -> List[int]:
        return notes.select(spec, self.profile.lowest_note, self.profile.highest_note)

    # -- painting -------------------------------------------------------
    def paint(self, midi_notes: Iterable[int], color: ColorValue) -> "Frame":
        for n in midi_notes:
            i = self.index(n)
            if i is not None:
                self.keys[i] = color
        return self

    def fill(self, color: ColorValue) -> "Frame":
        self.keys = [color] * len(self.keys)
        return self

    def gradient(self, midi_notes: Sequence[int], stops: Sequence[ColorValue]) -> "Frame":
        midi_notes = list(midi_notes)
        for n, value in zip(midi_notes, colors.gradient(stops, len(midi_notes))):
            self.paint([n], value)
        return self

    def scale(
        self,
        midi_notes: Iterable[int],
        root: int,
        intervals: Sequence[int],
        color: ColorValue,
        root_color: Optional[ColorValue] = None,
        other: Optional[ColorValue] = None,
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
        layers = preset.get("layers", []) if isinstance(preset, dict) else preset
        for layer in layers:
            self.apply_layer(layer)
        return self

    @classmethod
    def from_preset_file(cls, profile: Profile, path: Union[str, Path]) -> "Frame":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(profile).apply_preset(data)

    # -- output ---------------------------------------------------------
    def to_report(self) -> bytes:
        return self.profile.keys_report(self.keys)
