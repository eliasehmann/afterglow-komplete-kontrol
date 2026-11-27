"""Run Kontrol LEDs inside a web page (Pyodide).

The same engine, scenes and profiles as the desktop app. The page's JavaScript
(webapp/kkleds-web.js) gives us a small `bridge` object:

    bridge.hidDevice()          -> {vendorId, productId, productName} or null
    bridge.sendReport(id, data) -> queue a WebHID output report
    bridge.midiName()           -> name of the MIDI input in use, or null

and drives us by calling Runtime.step(), Runtime.api(), Runtime.hid_changed()
and Runtime.midi_message(). Nothing here imports anything browser-specific at
module level, so it's testable in normal Python with a fake bridge.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from .api import dispatch
from .device import DeviceError, _Base
from .engine import SETTINGS_FILE, Engine, EngineError
from .profile import load_registry


def _to_js(data: bytes):
    try:
        from pyodide.ffi import to_js  # type: ignore
    except ImportError:  # tests
        return data
    return to_js(data)


class WebDevice(_Base):
    """Sends reports through the page's WebHID connection."""

    def __init__(self, profile, bridge):
        self.profile = profile
        self.bridge = bridge
        self.info = {"product_string": profile.name}

    def write_report(self, data: bytes) -> None:
        data = bytes(data)
        self.bridge.sendReport(data[0], _to_js(data[1:]))

    def write_keys(self, keys) -> None:
        self.write_report(self.profile.keys_report(keys))

    def write_buttons(self, values) -> None:
        self.write_report(self.profile.buttons_report(values))


class _Msg:
    def __init__(self, type: str, **fields):
        self.type = type
        self.__dict__.update(fields)


def parse_midi(data: List[int]) -> Optional[_Msg]:
    """The few MIDI messages play-along needs, from raw Web MIDI bytes."""
    if len(data) < 3:
        return None
    status, a, b = data[0] & 0xF0, data[1], data[2]
    if status == 0x90:
        return _Msg("note_on", note=a, velocity=b)
    if status == 0x80:
        return _Msg("note_off", note=a, velocity=b)
    if status == 0xB0:
        return _Msg("control_change", control=a, value=b)
    return None


class WebMidiPort:
    """Looks like a mido input port to the engine; the page pushes messages in."""

    def __init__(self):
        self.queue: List[_Msg] = []

    def iter_pending(self):
        q, self.queue = self.queue, []
        return iter(q)

    def close(self) -> None:
        self.queue = []


class Runtime:
    def __init__(self, bridge, settings_file=SETTINGS_FILE):
        self.bridge = bridge
        self.port: Optional[WebMidiPort] = None
        self.engine = Engine(load_registry(), settings_file=settings_file, device_factory=self._open_device)
        self.engine._open_play = self._open_play
        # Play-along needs a MIDI permission click in the browser, so it starts off.
        self.engine.play["enabled"] = False
        self.engine.play_error = None

    # -- keyboard -------------------------------------------------------------
    def _open_device(self, registry, requested, path):
        d = self.bridge.hidDevice()
        if d is None:
            raise DeviceError("Click “Connect keyboard” and choose your Komplete Kontrol.")
        profile = requested or registry.by_usb(int(d.vendorId), int(d.productId))
        if profile is None:
            raise DeviceError(f"{d.productName} isn't supported yet.")
        dev = WebDevice(profile, self.bridge)
        for report in profile.init:
            dev.write_report(report)
        return dev

    def hid_changed(self) -> None:
        """The page connected, lost or switched the keyboard."""
        self.engine.connect()

    # -- play-along -------------------------------------------------------------
    def _open_play(self) -> Dict[str, Any]:
        name = self.bridge.midiName()
        if name is None:
            raise EngineError("Allow MIDI access in the browser to light up what you play.")
        self.port = WebMidiPort()
        return {"port": self.port, "name": str(name), "lit": {}, "sustained": set(), "pedal": False}

    def midi_message(self, data) -> None:
        if hasattr(data, "to_py"):
            data = data.to_py()
        msg = parse_midi([int(x) for x in data])
        if msg is not None and self.port is not None and self.engine._play is not None:
            self.port.queue.append(msg)

    # -- driving ----------------------------------------------------------------
    def step(self) -> float:
        return self.engine.step(time.monotonic())

    def api(self, method: str, path: str, body: str = "") -> str:
        try:
            parsed = json.loads(body) if body else {}
        except ValueError as e:
            return json.dumps({"code": 400, "body": {"error": f"bad JSON: {e}"}})
        code, obj = dispatch(self.engine, method, path, parsed if isinstance(parsed, dict) else {})
        return json.dumps({"code": code, "body": obj})
