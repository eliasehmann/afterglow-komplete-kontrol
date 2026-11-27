"""Button LEDs and touch strips, for any device whose profile has a "buttons" section.

The slot map, groups and which LEDs take colour all come from the profile.
This module only adds the value vocabulary (on / dim / colours), a frame to
paint into, and a small state file so separate commands don't wipe each other.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import colors
from .colors import ColorValue, Rgb
from .profile import ButtonSection, Profile

ON = colors.make("white", 2)
MEDIUM = colors.make("white", 1)
DIM = colors.make("white", 0)
_WORDS = {"on": ON, "bright": ON, "medium": MEDIUM, "mid": MEDIUM, "dim": DIM, "off": colors.OFF}


def parse_value(text) -> ColorValue:
    """'on', 'dim', 'medium', 'off', or any colour spec ('red', 'blue:0', '#ff8800')."""
    if isinstance(text, str) and text.strip().lower() in _WORDS:
        return _WORDS[text.strip().lower()]
    return colors.parse(text)


class ButtonFrame:
    def __init__(self, section: ButtonSection, fill: ColorValue = colors.OFF):
        self.section = section
        self.values: List[ColorValue] = [fill] * section.size

    def set(self, spec: str, value: ColorValue, extra: Optional[Dict[str, int]] = None) -> "ButtonFrame":
        for slot in self.section.resolve(spec, extra):
            self.values[slot] = value
        return self

    def _require_strip(self):
        if not self.section.strip:
            raise ValueError("this device's profile has no touch strip")
        return self.section.strip

    def strip(self, stops: Sequence[ColorValue]) -> "ButtonFrame":
        """Fill the touch strip with one colour or a gradient."""
        strip = self._require_strip()
        for slot, value in zip(strip, colors.gradient(stops, len(strip))):
            self.values[slot] = value
        return self

    def strip_bar(self, fraction: float, color: ColorValue, background: ColorValue = colors.OFF) -> "ButtonFrame":
        """Light the first `fraction` (0..1) of the strip, like a level meter."""
        strip = self._require_strip()
        lit = round(max(0.0, min(1.0, fraction)) * len(strip))
        for n, slot in enumerate(strip):
            self.values[slot] = color if n < lit else background
        return self

    def apply(self, mapping: Dict[str, object]) -> "ButtonFrame":
        """Preset form: {"display": "blue", "play": "on", "strip": ["red", "blue"]}."""
        for spec, value in mapping.items():
            if spec.strip().lower() == "strip" and isinstance(value, list) and value and not isinstance(value[0], int):
                self.strip([parse_value(v) for v in value])
            else:
                self.set(spec, parse_value(value))
        return self


# --------------------------------------------------------------------------- state
STATE_DIR = Path.home() / ".kkleds" / "state"


def state_file(profile: Profile) -> Path:
    return STATE_DIR / f"{profile.key}-buttons.json"


def _dump(v: ColorValue):
    return list(v) if isinstance(v, Rgb) else int(v)


def _load(v) -> ColorValue:
    return Rgb(*v) if isinstance(v, list) else int(v) & 0x7F


def load_state(profile: Profile) -> ButtonFrame:
    """Last button values sent to this device, so commands can change one thing at a time."""
    frame = ButtonFrame(profile.require_buttons())
    try:
        values = json.loads(state_file(profile).read_text(encoding="utf-8"))["values"]
        for i, v in enumerate(values[: len(frame.values)]):
            frame.values[i] = _load(v)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return frame


def save_state(profile: Profile, frame: ButtonFrame) -> None:
    try:
        path = state_file(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"values": [_dump(v) for v in frame.values]}), encoding="utf-8")
    except OSError:
        pass


def load_extra_names(path: Optional[str]) -> Dict[str, int]:
    """Optional extra names (the {"leds": {...}} format `kkleds buttons-probe` writes)."""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    leds = data.get("leds") or (data.get("buttons") or {}).get("leds") or {}
    return {k: int(v, 0) if isinstance(v, str) else int(v) for k, v in leds.items()}
