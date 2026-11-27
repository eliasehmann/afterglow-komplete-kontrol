"""The web version: browser runtime (with a fake WebHID/Web MIDI bridge) and the site build."""

import json
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest

from kkleds import buttons as btn
from kkleds import engine as engine_mod
from kkleds.browser import Runtime, parse_midi

ROOT = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(btn, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(engine_mod, "USER_PRESETS", tmp_path / "presets")
    monkeypatch.setattr(engine_mod, "USER_SCENES", tmp_path / "scenes")


class FakeBridge:
    def __init__(self):
        self.device = None
        self.sent = []
        self.midi = None

    def hidDevice(self):
        return self.device

    def sendReport(self, report_id, data):
        self.sent.append((report_id, bytes(data)))

    def midiName(self):
        return self.midi


S88 = types.SimpleNamespace(vendorId=0x17CC, productId=0x1630, productName="KOMPLETE KONTROL S88 MK2")


def call(rt, method, path, body=None):
    res = json.loads(rt.api(method, path, json.dumps(body) if body is not None else ""))
    return res["code"], res["body"]


def test_runs_without_a_keyboard_until_connected(tmp_path):
    bridge = FakeBridge()
    rt = Runtime(bridge, settings_file=tmp_path / "settings.json")
    code, st = call(rt, "GET", "/api/state")
    assert code == 200 and st["connected"] is False and "Connect keyboard" in st["error"]

    bridge.device = S88
    rt.hid_changed()  # the page connected the keyboard
    assert call(rt, "GET", "/api/state")[1]["connected"] is True
    assert bridge.sent[0] == (0xA0, b"\x00\x00")  # light guide init first

    bridge.sent.clear()
    code, _ = call(rt, "POST", "/api/mode", {"mode": "solid", "color": "red"})
    assert code == 200
    rt.step()
    keys = [s for s in bridge.sent if s[0] == 0x81]
    assert keys and keys[-1][1] == bytes([0x06]) * 88


def test_play_along_through_web_midi(tmp_path):
    bridge = FakeBridge()
    bridge.device = S88
    rt = Runtime(bridge, settings_file=tmp_path / "settings.json")
    code, body = call(rt, "POST", "/api/play", {"enabled": True})
    assert code == 400 and "Allow MIDI" in body["error"]  # no permission yet
    bridge.midi = "KOMPLETE KONTROL - 1"
    assert call(rt, "POST", "/api/play", {"enabled": True})[0] == 200
    rt.midi_message([0x90, 60, 127])
    rt.step()
    assert call(rt, "GET", "/api/state")[1]["keys"][60 - 21] == "#ffffff"  # pressed keys light up white
    rt.midi_message([0x80, 60, 0])
    rt.step()
    assert call(rt, "GET", "/api/state")[1]["keys"][60 - 21] == "#000000"


def test_settings_survive_a_reload(tmp_path):
    bridge = FakeBridge()
    bridge.device = S88
    rt = Runtime(bridge, settings_file=tmp_path / "settings.json")
    call(rt, "POST", "/api/mode", {"mode": "scene", "scene": "ocean"})
    rt.engine.close()
    again = Runtime(FakeBridge(), settings_file=tmp_path / "settings.json")
    assert call(again, "GET", "/api/state")[1]["mode"]["scene"] == "ocean"
    assert again.engine.play["enabled"] is False  # play-along always needs a click in the browser


def test_unsupported_keyboard_is_explained(tmp_path):
    bridge = FakeBridge()
    bridge.device = types.SimpleNamespace(vendorId=0x17CC, productId=0x9999, productName="Mystery Board")
    rt = Runtime(bridge, settings_file=tmp_path / "s.json")
    assert "isn't supported" in call(rt, "GET", "/api/state")[1]["error"]


def test_parse_midi():
    assert parse_midi([0x91, 64, 100]).type == "note_on"
    assert parse_midi([0x80, 64, 0]).type == "note_off"
    cc = parse_midi([0xB0, 64, 127])
    assert (cc.type, cc.control, cc.value) == ("control_change", 64, 127)
    assert parse_midi([0xF8]) is None and parse_midi([0xE0, 0, 64]) is None


def test_site_build(tmp_path):
    out = tmp_path / "site"
    subprocess.run([sys.executable, str(ROOT / "webapp" / "build.py"), str(out)], check=True, capture_output=True)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert '<script src="kkleds-web.js"></script>' in html and "Kontrol LEDs" in html
    assert html.index("kkleds-web.js") < html.index('"use strict"')  # glue loads before the page's script
    for name in ("kkleds-web.js", "kkleds.zip", "icon.png", ".nojekyll"):
        assert (out / name).exists(), name
    names = zipfile.ZipFile(out / "kkleds.zip").namelist()
    assert "kkleds/browser.py" in names and "kkleds/profiles/komplete-kontrol-s-mk2.json" in names
    assert "kkleds/presets/split-keyboard.json" in names
    assert "kkleds/app.py" not in names and "kkleds/webui.py" not in names

    # The zipped package must work without the desktop-only bits a browser lacks.
    probe = (
        "import sys; sys.modules.update({m: None for m in ('http.server', 'webbrowser', 'hid', 'mido', 'pystray', 'PIL')});"
        f"sys.path.insert(0, {str(out / 'kkleds.zip')!r});"
        "import kkleds, kkleds.browser;"
        "print('ok' if 'kkleds.zip' in kkleds.__file__ else 'imported from the wrong place: ' + kkleds.__file__)"
    )
    run = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True)
    assert run.stdout.strip() == "ok", run.stderr
