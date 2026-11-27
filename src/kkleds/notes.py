"""Note names, key selectors and scales. Uses the C4 = MIDI 60 convention."""

from __future__ import annotations

import re
from typing import List, Sequence

NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_LETTER = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
_NOTE_RE = re.compile(r"^([a-g])([#b]*)(-?\d+)?$", re.IGNORECASE)
_RANGE_RE = re.compile(r"^(.+?\d)(?:-|\.\.)(.+)$")

SCALES = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
    "melodic_minor": (0, 2, 3, 5, 7, 9, 11),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "locrian": (0, 1, 3, 5, 6, 8, 10),
    "major_pentatonic": (0, 2, 4, 7, 9),
    "minor_pentatonic": (0, 3, 5, 7, 10),
    "blues": (0, 3, 5, 6, 7, 10),
    "whole_tone": (0, 2, 4, 6, 8, 10),
    "chromatic": tuple(range(12)),
}
SCALE_ALIASES = {"ionian": "major", "aeolian": "minor", "natural_minor": "minor"}

BLACK_PCS = frozenset({1, 3, 6, 8, 10})


def _parse(spec: str):
    m = _NOTE_RE.match(spec.strip())
    if not m:
        raise ValueError(f"bad note {spec!r} (examples: C4, F#2, Bb3, 60)")
    letter, accidentals, octave = m.groups()
    pc = _LETTER[letter.lower()] + accidentals.count("#") - accidentals.lower().count("b")
    return pc, octave


def pitch_class(spec: str) -> int:
    """'C' -> 0, 'F#' -> 6, 'Bb' -> 10."""
    pc, octave = _parse(spec)
    if octave is not None:
        raise ValueError(f"expected a note name without octave, got {spec!r}")
    return pc % 12


def to_midi(spec) -> int:
    """'C4' -> 60, 'A0' -> 21, '60' -> 60."""
    if isinstance(spec, int):
        return spec
    s = spec.strip()
    if s.lstrip("-").isdigit():
        return int(s)
    pc, octave = _parse(s)
    if octave is None:
        raise ValueError(f"note {spec!r} needs an octave, e.g. {spec}4")
    return (int(octave) + 1) * 12 + pc


def name(midi: int) -> str:
    return f"{NAMES[midi % 12]}{midi // 12 - 1}"


def is_black(midi: int) -> bool:
    return midi % 12 in BLACK_PCS


def scale(name_: str) -> Sequence[int]:
    key = name_.strip().lower().replace("-", "_").replace(" ", "_")
    key = SCALE_ALIASES.get(key, key)
    if key not in SCALES:
        raise ValueError(f"unknown scale {name_!r}; choose from: {', '.join(SCALES)}")
    return SCALES[key]


def select(spec: str, lowest: int, highest: int) -> List[int]:
    """Resolve a key selector to a sorted list of MIDI notes on the keyboard.

    Selectors: "all", "white", "black", a pitch class ("C", "F#" = every C / F#),
    a single note ("C4", "60"), or a range ("A0-C4", "C4..C5", "21-60").
    Several selectors can be joined with commas: "C,E,G" or "A0-B1,C7-C8".
    """
    notes = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        low = part.lower()
        if low == "all":
            notes.update(range(lowest, highest + 1))
        elif low == "white":
            notes.update(n for n in range(lowest, highest + 1) if not is_black(n))
        elif low == "black":
            notes.update(n for n in range(lowest, highest + 1) if is_black(n))
        elif _NOTE_RE.match(part) and _parse(part)[1] is None:
            pc = pitch_class(part)
            notes.update(n for n in range(lowest, highest + 1) if n % 12 == pc)
        else:
            m = _RANGE_RE.match(part)
            if m:
                a, b = to_midi(m.group(1)), to_midi(m.group(2))
                a, b = min(a, b), max(a, b)
            else:
                a = b = to_midi(part)
            notes.update(n for n in range(a, b + 1) if lowest <= n <= highest)
    return sorted(notes)
