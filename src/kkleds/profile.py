"""Device profiles: everything product-specific lives in a JSON file.

A profile file describes a product family. Each entry under "variants" is one
concrete device (a USB product ID); it inherits everything from the family and
can override any part of it. See docs/adding-a-device.md for the format.

Profiles are loaded from, in order (later ones win on the same key or USB ID):

1. src/kkleds/profiles/*.json      (built in)
2. ~/.kkleds/profiles/*.json       (your own)
3. --profile FILE                  (given on the command line)
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, Iterator, List, Optional, Sequence, Tuple

from . import colors
from .colors import ColorValue
from .encodings import Encoding, from_spec

NI_VENDOR_ID = 0x17CC
BUILTIN_DIR = Path(__file__).parent / "profiles"
USER_DIR = Path.home() / ".kkleds" / "profiles"
DEFAULT_DRY_RUN = "s88-mk2"
STATUSES = ("verified", "partial", "unverified")


class ProfileError(ValueError):
    pass


def _int(value, what: str) -> int:
    if isinstance(value, bool):
        raise ProfileError(f"{what}: expected a number, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            pass
    raise ProfileError(f"{what}: expected a number like 0x81 or 69, got {value!r}")


def _norm(name: str) -> str:
    return str(name).strip().lower().replace("_", "").replace("-", "").replace(" ", "")


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


# --------------------------------------------------------------------------- light guide
@dataclass(frozen=True)
class LightGuide:
    report: int
    keys: int
    lowest_note: int  # MIDI note of the leftmost key at the default octave
    encoding: Encoding

    @property
    def highest_note(self) -> int:
        return self.lowest_note + self.keys - 1

    def report_bytes(self, values: Sequence[ColorValue]) -> bytes:
        values = list(values[: self.keys]) + [colors.OFF] * max(0, self.keys - len(values))
        return bytes([self.report]) + self.encoding.encode_all(values)


# --------------------------------------------------------------------------- buttons
@dataclass
class ButtonSection:
    report: int
    size: int  # number of LED slots
    encoding: Encoding
    leds: Dict[str, int] = field(default_factory=dict)
    aliases: Dict[str, str] = field(default_factory=dict)
    groups: Dict[str, List[int]] = field(default_factory=dict)
    color_slots: Optional[FrozenSet[int]] = None  # None = unknown
    strip: Tuple[int, ...] = ()

    @classmethod
    def blank(cls, report: int, size: int, encoding: Optional[Encoding] = None) -> "ButtonSection":
        return cls(report, size, encoding or from_spec("palette"))

    @classmethod
    def from_json(cls, data: dict, where: str) -> "ButtonSection":
        section = cls(
            report=_int(data.get("report"), f"{where} buttons.report"),
            size=_int(data.get("size"), f"{where} buttons.size"),
            encoding=from_spec(data.get("encoding")),
        )
        for name, slot in (data.get("leds") or {}).items():
            section.leds[str(name)] = _int(slot, f"{where} buttons.leds.{name}")
        if data.get("strip"):
            first = _int(data["strip"].get("first"), f"{where} buttons.strip.first")
            length = _int(data["strip"].get("length"), f"{where} buttons.strip.length")
            section.strip = tuple(range(first, first + length))
            for n, slot in enumerate(section.strip, start=1):
                section.leds.setdefault(f"strip_{n}", slot)
        for alias, target in (data.get("aliases") or {}).items():
            if target not in section.leds:
                raise ProfileError(f"{where} buttons.aliases.{alias}: unknown LED {target!r}")
            section.aliases[str(alias)] = target

        strip = set(section.strip)
        section.groups["all"] = sorted({s for s in section.leds.values() if s not in strip})
        section.groups["everything"] = sorted(set(section.leds.values()))
        if section.strip:
            section.groups["strip"] = list(section.strip)
        for gname, members in (data.get("groups") or {}).items():
            try:
                section.groups[str(gname)] = section.resolve(",".join(str(m) for m in members))
            except ValueError as e:
                raise ProfileError(f"{where} buttons.groups.{gname}: {e}") from e
        if "color" in data:
            try:
                section.color_slots = frozenset(section.resolve(",".join(str(m) for m in data["color"])))
            except ValueError as e:
                raise ProfileError(f"{where} buttons.color: {e}") from e
            section.groups["color"] = sorted(section.color_slots)
            section.groups["mono"] = [s for s in section.groups["everything"] if s not in section.color_slots]
        return section

    def resolve(self, spec: str, extra: Optional[Dict[str, int]] = None) -> List[int]:
        """'play', 'display', 'dis3', 'strip_1', '29', or comma lists of those."""
        leds = {_norm(k): v for k, v in self.leds.items()}
        leds.update({_norm(a): self.leds[t] for a, t in self.aliases.items()})
        groups = {_norm(k): v for k, v in self.groups.items()}
        if "color" in groups:
            groups.setdefault("colour", groups["color"])
            groups.setdefault("rgb", groups["color"])
        extra_lookup = {_norm(k): v for k, v in (extra or {}).items()}
        slots: List[int] = []
        for part in str(spec).split(","):
            key = _norm(part)
            if not key:
                continue
            if key in groups:
                slots += groups[key]
            elif key in leds:
                slots.append(leds[key])
            elif key in extra_lookup:
                slots.append(extra_lookup[key])
            elif key.isdigit() and int(key) < self.size:
                slots.append(int(key))
            else:
                raise ValueError(f"unknown button {part.strip()!r}; run `kkleds buttons --list`")
        return slots

    def name_of(self, slot: int) -> str:
        for name, s in self.leds.items():
            if s == slot:
                return name
        return f"slot_{slot}"

    def kind(self, slot: int) -> str:
        if self.color_slots is None:
            return "?"
        return "colour" if slot in self.color_slots else "single colour"

    def report_bytes(self, values: Sequence[ColorValue]) -> bytes:
        values = list(values[: self.size]) + [colors.OFF] * max(0, self.size - len(values))
        return bytes([self.report]) + self.encoding.encode_all(values)


# --------------------------------------------------------------------------- profile
@dataclass
class Profile:
    key: str  # unique id, used with --model, e.g. "s88-mk2"
    family: str
    name: str
    status: str
    vendor_id: int
    product_id: int
    interface: Optional[int] = None
    usage_page: Optional[int] = None
    init: Tuple[bytes, ...] = ()
    light_guide: Optional[LightGuide] = None
    buttons: Optional[ButtonSection] = None
    source: str = ""
    notes: str = ""
    panel: Optional[dict] = None  # optional drawing of the control panel for the UI
    path: Optional[Path] = None

    @property
    def usb_id(self) -> str:
        return f"{self.vendor_id:04X}:{self.product_id:04X}"

    # Keyboard geometry, for code that only cares about keys.
    @property
    def key_count(self) -> int:
        return self.light_guide.keys if self.light_guide else 0

    @property
    def lowest_note(self) -> int:
        return self.light_guide.lowest_note if self.light_guide else 0

    @property
    def highest_note(self) -> int:
        return self.light_guide.highest_note if self.light_guide else 0

    def require_light_guide(self) -> LightGuide:
        if self.light_guide is None:
            raise ProfileError(f"{self.name} has no light guide in its profile")
        return self.light_guide

    def require_buttons(self) -> ButtonSection:
        if self.buttons is None:
            raise ProfileError(f"{self.name} has no button LEDs in its profile")
        return self.buttons

    def keys_report(self, values: Sequence[ColorValue]) -> bytes:
        return self.require_light_guide().report_bytes(values)

    def buttons_report(self, values: Sequence[ColorValue]) -> bytes:
        return self.require_buttons().report_bytes(values)


def _build(key: str, family: str, data: dict, path: Optional[Path]) -> Profile:
    where = f"{path.name if path else '<profile>'} [{key}]"
    if "product_id" not in data:
        raise ProfileError(f"{where}: product_id is missing")
    hid = data.get("hid") or {}
    lg = data.get("light_guide")
    light_guide = None
    if lg:
        for req in ("report", "keys", "lowest_note"):
            if req not in lg:
                raise ProfileError(f"{where}: light_guide.{req} is missing")
        light_guide = LightGuide(
            report=_int(lg["report"], f"{where} light_guide.report"),
            keys=_int(lg["keys"], f"{where} light_guide.keys"),
            lowest_note=_int(lg["lowest_note"], f"{where} light_guide.lowest_note"),
            encoding=from_spec(lg.get("encoding")),
        )
    status = data.get("status", "unverified")
    if status not in STATUSES:
        raise ProfileError(f"{where}: status must be one of {', '.join(STATUSES)}")
    return Profile(
        key=key,
        family=family,
        name=data.get("name", key),
        status=status,
        vendor_id=_int(data.get("vendor_id", NI_VENDOR_ID), f"{where} vendor_id"),
        product_id=_int(data["product_id"], f"{where} product_id"),
        interface=None if hid.get("interface") is None else _int(hid["interface"], f"{where} hid.interface"),
        usage_page=None if hid.get("usage_page") is None else _int(hid["usage_page"], f"{where} hid.usage_page"),
        init=tuple(bytes(_int(b, f"{where} init") for b in seq) for seq in data.get("init") or []),
        light_guide=light_guide,
        buttons=ButtonSection.from_json(data["buttons"], where) if data.get("buttons") else None,
        source=data.get("source", ""),
        notes=data.get("notes", ""),
        panel=data.get("panel") or None,
        path=path,
    )


PANEL_BLOCKS = ("buttons", "strip", "screens", "knobs", "encoder", "wheels")


def panel_blocks(panel: dict) -> List[dict]:
    """All blocks of a panel drawing: the top panel's columns plus the side module."""
    blocks = [block for column in panel.get("columns", []) for block in column]
    return blocks + list(panel.get("side", []))


def panel_names(panel: dict) -> List[str]:
    """Every LED name a panel drawing refers to ("#name" = drawn only, not controllable)."""
    names = []
    for block in panel_blocks(panel):
        if block.get("type") == "buttons":
            names += [n for row in block.get("rows", []) for n in row]
        elif block.get("type") == "encoder":
            names += [block[d] for d in ("up", "down", "left", "right") if d in block]
    return names


def load_data(data: dict, path: Optional[Path] = None) -> List[Profile]:
    family = str(data.get("id") or (path.stem if path else "profile"))
    base = {k: v for k, v in data.items() if k != "variants"}
    variants = data.get("variants") or {family: {}}
    return [_build(str(key), family, _merge(base, override or {}), path) for key, override in variants.items()]


def load_file(path) -> List[Profile]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ProfileError(f"could not read profile {path}: {e}") from e
    return load_data(data, path)


def validate(profile: Profile) -> List[str]:
    """Range and consistency checks beyond what loading already enforces."""
    problems = []
    if not 0 <= profile.vendor_id <= 0xFFFF or not 0 <= profile.product_id <= 0xFFFF:
        problems.append("vendor_id/product_id must be 16-bit")
    for seq in profile.init:
        if not seq:
            problems.append("init contains an empty report")
    lg = profile.light_guide
    if lg:
        if not 0 <= lg.report <= 0xFF:
            problems.append(f"light_guide.report 0x{lg.report:X} is not a byte")
        if lg.keys <= 0:
            problems.append("light_guide.keys must be positive")
        if not 0 <= lg.lowest_note or lg.highest_note > 127:
            problems.append(f"keys {lg.lowest_note}..{lg.highest_note} fall outside MIDI 0-127")
    b = profile.buttons
    if b:
        if not 0 <= b.report <= 0xFF:
            problems.append(f"buttons.report 0x{b.report:X} is not a byte")
        for name, slot in b.leds.items():
            if not 0 <= slot < b.size:
                problems.append(f"buttons.leds.{name} = {slot} is outside 0..{b.size - 1}")
        seen: Dict[int, str] = {}
        for name, slot in b.leds.items():
            if slot in seen and not (name.startswith("strip_") or seen[slot].startswith("strip_")):
                problems.append(f"buttons.leds: {seen[slot]!r} and {name!r} share slot {slot}")
            seen.setdefault(slot, name)
    if lg is None and b is None:
        problems.append("profile has neither a light_guide nor buttons")
    if profile.panel:
        if b is None:
            problems.append("panel needs a buttons section")
        else:
            for block in panel_blocks(profile.panel):
                if block.get("type") not in PANEL_BLOCKS:
                    problems.append(f"panel block type {block.get('type')!r} is not one of {', '.join(PANEL_BLOCKS)}")
            for name in panel_names(profile.panel):
                if not name.startswith("#") and name not in b.leds:
                    problems.append(f"panel refers to unknown LED {name!r} (use '#{name}' for a drawn-only control)")
    return problems


# --------------------------------------------------------------------------- registry
class Registry:
    def __init__(self):
        self._profiles: Dict[str, Profile] = {}

    def add(self, profiles: Iterable[Profile]) -> None:
        for p in profiles:
            self._profiles.pop(p.key, None)  # re-insert so later sources win lookups
            self._profiles[p.key] = p

    def add_file(self, path) -> None:
        self.add(load_file(path))

    def add_dir(self, directory: Path) -> None:
        for path in sorted(Path(directory).glob("*.json")):
            self.add_file(path)

    def __iter__(self) -> Iterator[Profile]:
        return iter(self._profiles.values())

    def __len__(self) -> int:
        return len(self._profiles)

    def get(self, key: str) -> Profile:
        k = key.strip().lower()
        if k in self._profiles:
            return self._profiles[k]
        matches = [p for p in self if p.key.startswith(k) or _norm(p.key) == _norm(k)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ProfileError(f"{key!r} is ambiguous: {', '.join(p.key for p in matches)}")
        raise ProfileError(f"no profile {key!r}; known: {', '.join(p.key for p in self)} (see `kkleds devices`)")

    def by_usb(self, vendor_id: int, product_id: int) -> Optional[Profile]:
        for p in reversed(list(self._profiles.values())):
            if p.vendor_id == vendor_id and p.product_id == product_id:
                return p
        return None


def load_registry(extra_files: Sequence = ()) -> Registry:
    reg = Registry()
    reg.add_dir(BUILTIN_DIR)
    if USER_DIR.is_dir():
        reg.add_dir(USER_DIR)
    for f in extra_files:
        reg.add_file(f)
    return reg
