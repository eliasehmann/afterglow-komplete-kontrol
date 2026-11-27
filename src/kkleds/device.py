"""Talking to the keyboard over HID (or pretending to, in dry-run mode)."""

from __future__ import annotations

import os
import sys
from typing import List, Optional, Sequence

from . import colors, notes, protocol
from .layout import MODELS_BY_PID, S88, Model


class DeviceError(RuntimeError):
    pass


def _hid():
    try:
        import hid  # provided by the "hidapi" package
    except ImportError as e:
        raise DeviceError("The 'hidapi' package is missing. Run: pip install hidapi") from e
    return hid


def _path_str(path) -> str:
    return path.decode(errors="replace") if isinstance(path, (bytes, bytearray)) else str(path)


def list_interfaces() -> List[dict]:
    """Every HID interface exposed by any Native Instruments device."""
    return list(_hid().enumerate(protocol.NI_VENDOR_ID, 0))


def _pick(candidates: List[dict]) -> dict:
    for d in candidates:
        if d.get("interface_number") == protocol.HID_INTERFACE:
            return d
    for d in candidates:
        if d.get("usage_page", 0) >= 0xFF00:
            return d
    return candidates[0]


class HidDevice:
    def __init__(self, handle, info: dict, model: Model):
        self._h = handle
        self.info = info
        self.model = model

    @classmethod
    def open(cls, model: Optional[Model] = None, path: Optional[str] = None) -> "HidDevice":
        hid = _hid()
        infos = list_interfaces()
        if path:
            chosen = next((d for d in infos if _path_str(d["path"]) == path), None)
            if chosen is None:
                raise DeviceError(f"No NI HID interface with path {path!r}. Run `kkleds list`.")
        else:
            candidates = [
                d
                for d in infos
                if d["product_id"] in MODELS_BY_PID and (model is None or d["product_id"] == model.pid)
            ]
            if not candidates:
                raise DeviceError(
                    "No Komplete Kontrol S-series MK2 found. Is it plugged in and powered on? "
                    "Run `kkleds list` to see what is visible, or add --dry-run to preview."
                )
            chosen = _pick(candidates)

        handle = hid.device()
        try:
            handle.open_path(chosen["path"])
        except (OSError, IOError) as e:
            raise DeviceError(
                f"Could not open {_path_str(chosen['path'])}: {e}. If NI software holds the "
                "device, run scripts\\ni-services.ps1 stop (as admin) and try again."
            ) from e
        detected = MODELS_BY_PID.get(chosen["product_id"], S88)
        dev = cls(handle, chosen, model or detected)
        dev.write_report(protocol.LIGHT_GUIDE_INIT)
        return dev

    def write_report(self, data: bytes) -> None:
        written = self._h.write(list(data))
        if written is None or written < 0:
            try:
                reason = self._h.error()
            except Exception:
                reason = "unknown error"
            raise DeviceError(f"HID write of report 0x{data[0]:02X} failed: {reason}")

    def write_keys(self, keys: Sequence[int]) -> None:
        payload = [c & protocol.MAX_VALUE for c in keys[: self.model.key_count]]
        payload += [0] * (self.model.key_count - len(payload))
        self.write_report(bytes([protocol.LIGHT_GUIDE_REPORT, *payload]))

    def write_buttons(self, values: Sequence[int]) -> None:
        self.write_report(bytes([protocol.BUTTON_REPORT, *(v & protocol.MAX_VALUE for v in values)]))

    def close(self) -> None:
        try:
            self._h.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


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


class DryRunDevice:
    """Renders the light guide in the terminal instead of sending it over USB."""

    def __init__(self, model: Model = S88, stream=None):
        self.model = model
        self.stream = stream or sys.stdout
        self.info = {"product_string": f"{model.name} (dry run)"}
        self._dirty_line = False
        _enable_ansi()
        enc = getattr(self.stream, "encoding", None) or "ascii"
        try:
            "█▄".encode(enc)
            self._white, self._black = "█", "▄"
        except (UnicodeEncodeError, LookupError):
            self._white, self._black = "#", "+"

    def render(self, keys: Sequence[int]) -> str:
        out = []
        for i, value in enumerate(keys):
            r, g, b = colors.to_rgb(value)
            glyph = self._black if notes.is_black(self.model.lowest_note + i) else self._white
            if value == colors.OFF:
                out.append(f"\x1b[38;2;60;60;60m{'.' if glyph == self._white else ','}")
            else:
                out.append(f"\x1b[38;2;{r};{g};{b}m{glyph}")
        return "".join(out) + "\x1b[0m"

    def write_keys(self, keys: Sequence[int]) -> None:
        self.stream.write("\r" + self.render(keys))
        self.stream.flush()
        self._dirty_line = True

    def write_buttons(self, values: Sequence[int]) -> None:
        self._newline()
        lit = {i: v for i, v in enumerate(values) if v}
        self.stream.write(f"[dry-run] button report 0x80: {lit or 'all off'}\n")

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

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
