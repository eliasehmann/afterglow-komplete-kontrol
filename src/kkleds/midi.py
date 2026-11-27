"""MIDI input helpers (optional: needs the [midi] extra)."""

from __future__ import annotations

from typing import List, Optional


class MidiError(ValueError):
    pass


def load_mido():
    try:
        import mido
    except ImportError as e:
        raise MidiError("MIDI support is not installed: pip install -e .[midi]") from e
    return mido


def input_names() -> Optional[List[str]]:
    """MIDI input port names, or None if MIDI support isn't installed."""
    try:
        return load_mido().get_input_names()
    except MidiError:
        return None
    except Exception:
        return []


def pick_port(wanted: Optional[str] = None) -> str:
    """Find the keyboard's MIDI input (or the port matching `wanted`)."""
    ports = load_mido().get_input_names()
    if wanted:
        for p in ports:
            if wanted.lower() in p.lower():
                return p
        raise MidiError(f"No MIDI input matching {wanted!r}. Available: {ports}")
    for test in (lambda p: "mk2 midi" in p, lambda p: "kontrol" in p and "ext" not in p):
        for p in ports:
            if test(p.lower()):
                return p
    raise MidiError(f"Could not find the keyboard's MIDI input; choose a port. Available: {ports}")


VELOCITY_HUES = ("blue", "turquoise", "cyan", "mint", "green", "lime", "yellow",
                 "warm_yellow", "light_orange", "orange", "red")


def velocity_hue(velocity: int) -> str:
    return VELOCITY_HUES[max(0, min(127, velocity)) * len(VELOCITY_HUES) // 128]
