"""Talking to a keyboard over HID (or pretending to, in dry-run mode).

Nothing here is product-specific: which device to open, which init reports to
send and how to encode colours all come from the device's Profile.
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Sequence

from . import colors, notes
from .colors import ColorValue
from .profile import Profile, Registry


class DeviceError(RuntimeError):
    pass


def _hid():
    try:
        import hid  # provided by the "hidapi" package
    except ImportError as e:
        raise DeviceError("The 'hidapi' package is missing. Run: pip install hidapi") from e
    return hid


def path_str(path) -> str:
    return path.decode(errors="replace") if isinstance(path, (bytes, bytearray)) else str(path)


def list_interfaces(vendor_id: int = 0) -> List[dict]:
    """HID interfaces, all of them (vendor_id=0) or one vendor's."""
    return list(_hid().enumerate(vendor_id, 0))


def _pick_interface(candidates: List[dict], profile: Profile) -> dict:
    if profile.interface is not None:
        for d in candidates:
            if d.get("interface_number") == profile.interface:
                return d
    if profile.usage_page is not None:
        for d in candidates:
            if d.get("usage_page") == profile.usage_page:
                return d
    for d in candidates:
        if d.get("usage_page", 0) >= 0xFF00:  # vendor-defined
            return d
    return candidates[0]


class _Base:
    profile: Profile

    @property
    def model(self) -> Profile:  # older name
        return self.profile

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        pass


class HidDevice(_Base):
    def __init__(self, handle, info: dict, profile: Profile):
        self._h = handle
        self.info = info
        self.profile = profile

    @classmethod
    def open(cls, registry: Registry, profile: Optional[Profile] = None, path: Optional[str] = None) -> "HidDevice":
        hid = _hid()
        infos = list_interfaces()
        if path:
            chosen = next((d for d in infos if path_str(d["path"]) == path), None)
            if chosen is None:
                raise DeviceError(f"No HID interface with path {path!r}. Run `kkleds list`.")
            profile = profile or registry.by_usb(chosen["vendor_id"], chosen["product_id"])
            if profile is None:
                raise DeviceError(
                    f"No profile for USB {chosen['vendor_id']:04X}:{chosen['product_id']:04X}. "
                    f"Create one with `kkleds profile-new --pid 0x{chosen['product_id']:04X}`."
                )
        else:
            by_device: Dict[tuple, List[dict]] = {}
            for d in infos:
                usb = (d["vendor_id"], d["product_id"])
                if profile is not None:
                    match = usb == (profile.vendor_id, profile.product_id)
                else:
                    match = registry.by_usb(*usb) is not None
                if match:
                    by_device.setdefault(usb, []).append(d)
            if not by_device:
                what = profile.name if profile else "supported keyboard"
                raise DeviceError(
                    f"No {what} found. Is it plugged in and switched on? "
                    "Run `kkleds list` to see what is connected, or add --dry-run to preview."
                )
            usb, candidates = next(iter(by_device.items()))
            profile = profile or registry.by_usb(*usb)
            chosen = _pick_interface(candidates, profile)

        handle = hid.device()
        try:
            handle.open_path(chosen["path"])
        except (OSError, IOError) as e:
            raise DeviceError(
                f"Could not open {path_str(chosen['path'])}: {e}. If NI software holds the "
                "device, run scripts\\ni-services.ps1 stop (as admin) and try again."
            ) from e
        dev = cls(handle, chosen, profile)
        for report in profile.init:
            dev.write_report(report)
        return dev

    def write_report(self, data: bytes) -> None:
        written = self._h.write(list(data))
        if written is None or written < 0:
            try:
                reason = self._h.error()
            except Exception:
                reason = "unknown error"
            raise DeviceError(f"HID write of report 0x{data[0]:02X} failed: {reason}")

    def write_keys(self, keys: Sequence[ColorValue]) -> None:
        self.write_report(self.profile.keys_report(keys))

    def write_buttons(self, values: Sequence[ColorValue]) -> None:
        self.write_report(self.profile.buttons_report(values))

    def close(self) -> None:
        try:
            self._h.close()
        except Exception:
            pass


def _enable_ansi() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


class DryRunDevice(_Base):
    """Renders LEDs in the terminal instead of sending them over USB."""

    def __init__(self, profile: Profile, stream=None):
        self.profile = profile
        self.stream = stream or sys.stdout
        self.info = {"product_string": f"{profile.name} (dry run)"}
        self._dirty_line = False
        _enable_ansi()
        enc = getattr(self.stream, "encoding", None) or "ascii"
        try:
            "█▄".encode(enc)
            self._white, self._black = "█", "▄"
        except (UnicodeEncodeError, LookupError):
            self._white, self._black = "#", "+"

    def blocks(self, values: Sequence[ColorValue], first_note: Optional[int] = None) -> str:
        out = []
        for i, value in enumerate(values):
            black = first_note is not None and notes.is_black(first_note + i)
            glyph = self._black if black else self._white
            if colors.is_off(value):
                out.append(f"\x1b[38;2;60;60;60m{',' if black else '.'}")
            else:
                r, g, b = colors.preview(value)
                out.append(f"\x1b[38;2;{r};{g};{b}m{glyph}")
        return "".join(out) + "\x1b[0m"

    def render(self, keys: Sequence[ColorValue]) -> str:
        return self.blocks(keys, self.profile.lowest_note)

    def write_keys(self, keys: Sequence[ColorValue]) -> None:
        self.profile.keys_report(keys)  # same validation as the real device
        self.stream.write("\r" + self.render(keys))
        self.stream.flush()
        self._dirty_line = True

    def write_buttons(self, values: Sequence[ColorValue]) -> None:
        section = self.profile.require_buttons()
        self.profile.buttons_report(values)
        self._newline()
        strip = set(section.strip)
        lit = [
            f"{section.name_of(i)}={colors.describe(v)}"
            for i, v in enumerate(values)
            if i not in strip and not colors.is_off(v)
        ]
        line = f"[dry-run] buttons: {', '.join(lit) or 'all off'}"
        if section.strip:
            line += "  strip: " + self.blocks([values[s] for s in section.strip])
        self.stream.write(line + "\n")

    def write_report(self, data: bytes) -> None:
        self._newline()
        self.stream.write(f"[dry-run] raw report: {data.hex(' ')}\n")

    def _newline(self) -> None:
        if self._dirty_line:
            self.stream.write("\n")
            self._dirty_line = False

    def close(self) -> None:
        self._newline()
        self.stream.flush()
