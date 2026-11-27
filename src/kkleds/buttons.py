"""Button LEDs and the touch strip (HID report 0x80). Mapped on an S88 MK2.

Every byte in the report is a palette colour byte, the same format as the light
guide. LEDs come in two kinds:

* colour LEDs - Mute, Solo, display buttons 1-8, the 4-D encoder arrows and
  the 25 touch strip dots - show the full colour;
* single-colour LEDs - every other button - use only the level bits
  (0 dim, 1 medium, 2 bright; 3 looks bright too), whatever the hue.

Slots 42 and 43 show nothing, and nothing past 68 lights up. The octave -/+
lights are not in this report (the firmware drives them), and the knobs have
no LEDs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from . import colors

REPORT_SIZE = 69  # slots 0..68
STRIP_FIRST = 44
STRIP_LEN = 25
STRIP = tuple(range(STRIP_FIRST, STRIP_FIRST + STRIP_LEN))

LEDS: Dict[str, int] = {
    "mute": 0,
    "solo": 1,
    **{f"display_{n}": 1 + n for n in range(1, 9)},  # 2..9
    "encoder_left": 10,
    "encoder_up": 11,
    "encoder_down": 12,
    "encoder_right": 13,
    "shift": 14,
    "scale_edit": 15,
    "arp_edit": 16,
    "scene": 17,
    "undo_redo": 18,
    "quantize": 19,
    "auto": 20,
    "pattern": 21,
    "preset_up": 22,
    "track": 23,
    "loop": 24,
    "metro": 25,
    "tempo": 26,
    "preset_down": 27,
    "key_mode": 28,
    "play": 29,
    "rec": 30,
    "stop": 31,
    "preset_left": 32,
    "preset_right": 33,
    "clear": 34,
    "browser": 35,
    "plugin": 36,
    "mixer": 37,
    "instance": 38,
    "midi": 39,
    "setup": 40,
    "fixed_vel": 41,
    **{f"strip_{n}": STRIP_FIRST + n - 1 for n in range(1, STRIP_LEN + 1)},
}

ALIASES: Dict[str, str] = {
    **{f"dis{n}": f"display_{n}" for n in range(1, 9)},
    **{f"d{n}": f"display_{n}" for n in range(1, 9)},
    "4dleft": "encoder_left",
    "4dtop": "encoder_up",
    "4dup": "encoder_up",
    "4dbot": "encoder_down",
    "4ddown": "encoder_down",
    "4dright": "encoder_right",
    "record": "rec",
    "metronome": "metro",
    "scale": "scale_edit",
    "arp": "arp_edit",
    "undo": "undo_redo",
}

_BUTTONS = [i for i in range(42)]
GROUPS: Dict[str, List[int]] = {
    "all": _BUTTONS,
    "everything": _BUTTONS + list(STRIP),
    "strip": list(STRIP),
    "display": list(range(2, 10)),
    "encoder": [10, 11, 12, 13],
    "transport": [24, 25, 26, 29, 30, 31],
    "preset": [22, 27, 32, 33],
    "browse": [35, 36, 37, 38, 39, 40],
    "edit": [15, 16, 18, 19, 20],
    "color": list(range(14)) + list(STRIP),
    "mono": list(range(14, 42)),
}
GROUPS["colour"] = GROUPS["color"]
GROUPS["rgb"] = GROUPS["color"]

COLOR_LEDS = frozenset(GROUPS["color"])

ON = colors.make("white", 2)
DIM = colors.make("white", 0)
MEDIUM = colors.make("white", 1)
_WORDS = {"on": ON, "bright": ON, "medium": MEDIUM, "mid": MEDIUM, "dim": DIM, "off": colors.OFF}


def _norm(name: str) -> str:
    return name.strip().lower().replace("_", "").replace("-", "").replace(" ", "")


_LOOKUP: Dict[str, int] = {_norm(k): v for k, v in LEDS.items()}
_LOOKUP.update({_norm(a): LEDS[t] for a, t in ALIASES.items()})
_GROUP_LOOKUP = {_norm(k): v for k, v in GROUPS.items()}


def name_of(slot: int) -> str:
    for k, v in LEDS.items():
        if v == slot:
            return k
    return f"slot_{slot}"


def resolve(spec: str, extra: Optional[Dict[str, int]] = None) -> List[int]:
    """'play', 'display', 'dis3', 'strip_1', '29', or comma lists of those."""
    extra_lookup = {_norm(k): v for k, v in (extra or {}).items()}
    slots: List[int] = []
    for part in spec.split(","):
        key = _norm(part)
        if not key:
            continue
        if key in _GROUP_LOOKUP:
            slots += _GROUP_LOOKUP[key]
        elif key in _LOOKUP:
            slots.append(_LOOKUP[key])
        elif key in extra_lookup:
            slots.append(extra_lookup[key])
        elif key.isdigit() and int(key) < REPORT_SIZE:
            slots.append(int(key))
        else:
            raise ValueError(f"unknown button {part!r}; run `kkleds buttons --list`")
    return slots


def parse_value(text) -> int:
    """'on', 'dim', 'medium', 'off', or any colour spec ('red', 'blue:0', '0x2E')."""
    if isinstance(text, str) and text.strip().lower() in _WORDS:
        return _WORDS[text.strip().lower()]
    return colors.parse(text)


class ButtonFrame:
    def __init__(self, fill: int = colors.OFF):
        self.values: List[int] = [fill] * REPORT_SIZE

    def set(self, spec: str, value: int, extra: Optional[Dict[str, int]] = None) -> "ButtonFrame":
        for slot in resolve(spec, extra):
            self.values[slot] = value
        return self

    def strip(self, stops: Sequence[int]) -> "ButtonFrame":
        """Fill the touch strip with one colour or a gradient."""
        for slot, value in zip(STRIP, colors.gradient(stops, STRIP_LEN)):
            self.values[slot] = value
        return self

    def strip_bar(self, fraction: float, color: int, background: int = colors.OFF) -> "ButtonFrame":
        """Light the first `fraction` (0..1) of the strip, like a level meter."""
        lit = round(max(0.0, min(1.0, fraction)) * STRIP_LEN)
        for n, slot in enumerate(STRIP):
            self.values[slot] = color if n < lit else background
        return self

    def apply(self, mapping: Dict[str, object]) -> "ButtonFrame":
        """Preset form: {"display": "blue", "play": "on", "strip": ["red", "blue"]}."""
        for spec, value in mapping.items():
            if _norm(spec) == "strip" and isinstance(value, list):
                self.strip([parse_value(v) for v in value])
            else:
                self.set(spec, parse_value(value))
        return self


# --------------------------------------------------------------------------- state
STATE_FILE = Path.home() / ".kkleds" / "buttons-state.json"


def load_state() -> ButtonFrame:
    """Last button values sent, so commands can change one thing at a time."""
    frame = ButtonFrame()
    try:
        values = json.loads(STATE_FILE.read_text(encoding="utf-8"))["values"]
        for i, v in enumerate(values[:REPORT_SIZE]):
            frame.values[i] = int(v) & 0x7F
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return frame


def save_state(frame: ButtonFrame) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"values": frame.values}), encoding="utf-8")
    except OSError:
        pass


def load_extra_names(path: Optional[str]) -> Dict[str, int]:
    """Optional user name map (the format `kkleds buttons-probe` writes)."""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    return {k: int(v) for k, v in json.loads(p.read_text(encoding="utf-8")).get("leds", {}).items()}
