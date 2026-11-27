"""The 1.0 features: remembering settings, play-along overlay, auto-reconnect,
live tile previews and the desktop app pieces."""

import json
import time

import pytest

from kkleds import buttons as btn
from kkleds import colors, engine as engine_mod, profile
from kkleds.engine import Engine

REG = profile.load_registry()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(btn, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(engine_mod, "USER_PRESETS", tmp_path / "presets")
    monkeypatch.setattr(engine_mod, "USER_SCENES", tmp_path / "scenes")


def tick(e, now=None):
    with e.lock:
        e._tick(now if now is not None else time.monotonic())


# ---- remembering the last look -------------------------------------------------
def test_last_look_comes_back(tmp_path):
    settings = tmp_path / "settings.json"
    e = Engine(REG, REG.get("s88-mk2"), dry_run=True, settings_file=settings)
    e.set_mode({"mode": "scene", "scene": "aurora", "speed": 0.5, "brightness": "medium"})
    e.set_hold(False)
    e.close()  # saves
    data = json.loads(settings.read_text())
    assert data["mode"]["scene"] == "aurora" and data["hold"] is False

    again = Engine(REG, REG.get("s88-mk2"), dry_run=True, settings_file=settings)
    assert again.state()["mode"] == {"mode": "scene", "scene": "aurora", "speed": 0.5, "brightness": "medium"}
    assert again.hold is False
    again.close()


def test_painting_is_remembered_too(tmp_path):
    settings = tmp_path / "settings.json"
    e = Engine(REG, REG.get("s88-mk2"), dry_run=True, settings_file=settings)
    e.set_mode({"mode": "custom", "keys": ["blue"] * 3})
    e.close()
    again = Engine(REG, REG.get("s88-mk2"), dry_run=True, settings_file=settings)
    assert again.state()["keys"][:4] == ["#0000ff"] * 3 + ["#000000"]
    again.close()


def test_broken_or_stale_settings_are_ignored(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{not json")
    assert Engine(REG, REG.get("s88-mk2"), dry_run=True, settings_file=settings).mode == {"mode": "off"}
    settings.write_text(json.dumps({"mode": {"mode": "preset", "name": "deleted-look"}}))
    e = Engine(REG, REG.get("s88-mk2"), dry_run=True, settings_file=settings)
    assert e.state()["mode"]["mode"] == "off"  # falls back instead of failing to start


# ---- light up what I play -------------------------------------------------------
class FakeMsg:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class FakePort:
    def __init__(self):
        self.queue = []
        self.closed = False

    def iter_pending(self):
        q, self.queue = self.queue, []
        return iter(q)

    def close(self):
        self.closed = True


@pytest.fixture
def played(monkeypatch):
    port = FakePort()
    e = Engine(REG, REG.get("s88-mk2"), dry_run=True)
    monkeypatch.setattr(e, "_open_play", lambda: {"port": port, "name": "Fake MIDI", "lit": {},
                                                  "sustained": set(), "pedal": False})
    yield e, port
    e.close()


def test_play_along_on_top_of_a_scene(played):
    e, port = played
    e.set_mode({"mode": "solid", "color": "blue:0"})
    e.set_play({"enabled": True, "velocity": True})
    assert e.state()["play"] == {"enabled": True, "velocity": True, "port": "Fake MIDI", "error": None}
    port.queue += [FakeMsg("note_on", note=60, velocity=127), FakeMsg("note_on", note=21, velocity=5)]
    tick(e)
    keys = e.state()["keys"]
    assert keys[60 - 21] == "#ff0000"   # hard = red
    assert keys[0] == "#0000ff"         # soft = blue
    assert keys[1] == "#000055"         # untouched key keeps the look
    port.queue.append(FakeMsg("note_off", note=60, velocity=0))
    tick(e)
    assert e.state()["keys"][60 - 21] == "#000055"


def test_play_along_is_white_by_default(played, tmp_path):
    e, port = played
    e.set_play({"enabled": True})
    port.queue.append(FakeMsg("note_on", note=60, velocity=127))
    tick(e)
    assert e.state()["keys"][60 - 21] == "#ffffff"
    # older saved settings with velocity colours don't bring them back
    settings = tmp_path / "old.json"
    settings.write_text(json.dumps({"play": {"enabled": False, "velocity": True, "color": "red"}}))
    restored = Engine(REG, REG.get("s88-mk2"), dry_run=True, settings_file=settings)
    assert restored.play["velocity"] is False and restored.play["color"] == "white"


def test_play_along_sustain_pedal(played):
    e, port = played
    e.set_play({"enabled": True, "velocity": False, "color": "green"})
    port.queue += [FakeMsg("control_change", control=64, value=127), FakeMsg("note_on", note=64, velocity=80),
                   FakeMsg("note_off", note=64, velocity=0)]
    tick(e)
    assert e.state()["keys"][64 - 21] == "#00ff00"  # held by the pedal
    port.queue.append(FakeMsg("control_change", control=64, value=0))
    tick(e)
    assert e.state()["keys"][64 - 21] == "#000000"


def test_play_along_off_closes_port(played):
    e, port = played
    e.set_play({"enabled": True})
    e.set_play({"enabled": False})
    assert port.closed and e.state()["play"]["enabled"] is False


def test_play_along_without_midi_gives_friendly_error(monkeypatch):
    e = Engine(REG, REG.get("s88-mk2"), dry_run=True)

    def boom():
        raise engine_mod.midi.MidiError("MIDI support is not installed")

    monkeypatch.setattr(engine_mod.midi, "load_mido", boom)
    with pytest.raises(ValueError, match="not installed"):
        e.set_play({"enabled": True})
    assert e.state()["play"]["enabled"] is False


def test_scene_keeps_its_frame_rate_while_play_along_runs(played):
    e, port = played
    e.set_mode({"mode": "scene", "scene": "fire"})
    e.set_play({"enabled": True})
    calls = []
    real = e._scene_tick
    e._scene_tick = lambda now: calls.append(now) or real(now)
    t0 = time.monotonic()
    for k in range(12):  # 12 ticks at 120 Hz = 0.1 s
        tick(e, t0 + k / 120)
    assert len(calls) == 3  # rendered at ~30 fps, not 120


# ---- reconnecting --------------------------------------------------------------
def test_reconnects_when_keyboard_comes_back(monkeypatch):
    attempts = []

    def fake_open(registry, prof=None, path=None):
        attempts.append(1)
        if len(attempts) < 3:
            raise engine_mod.DeviceError("No supported keyboard found")
        return engine_mod.MemoryDevice(REG.get("s88-mk2"))

    monkeypatch.setattr(engine_mod.HidDevice, "open", staticmethod(fake_open))
    monkeypatch.setattr(engine_mod, "RECONNECT_INTERVAL", 0.02)
    e = Engine(REG, None, dry_run=False)
    assert not e.state()["connected"]
    e.start()
    deadline = time.time() + 3
    while not e.state()["connected"] and time.time() < deadline:
        time.sleep(0.02)
    e.close()
    assert len(attempts) >= 3 and e.error is None


# ---- previews ------------------------------------------------------------------
def test_live_previews_follow_the_real_scenes():
    e = Engine(REG, REG.get("s88-mk2"), dry_run=True)
    e.save_scene("Mine", ["red", "blue"], "still")
    first = e.previews()
    assert set(first) == set(engine_mod.scenes.SCENES) | {"custom:Mine"}
    assert all(len(v) == Engine.PREVIEW_LEDS for v in first.values())
    assert first["custom:Mine"][0] == "#ff0000" and first["custom:Mine"][-1] == "#0000ff"
    # palette device: every preview colour is one the keyboard can actually show
    shown = {engine_mod.hex_of(v) for v in range(0x48)}
    assert all(h in shown for row in first.values() for h in row)
    time.sleep(0.3)
    assert e.previews()["rainbow"] != first["rainbow"]  # it moves
    e.delete_scene("Mine")
    assert "custom:Mine" not in e.previews()


# ---- app pieces ----------------------------------------------------------------
def test_second_server_cannot_steal_the_port():
    from kkleds.webui import make_server

    e = Engine(REG, REG.get("s88-mk2"), dry_run=True)
    first = make_server(e, 0)
    try:
        with pytest.raises(OSError):
            make_server(e, first.server_address[1])
    finally:
        first.server_close()


def test_app_hands_over_to_a_running_copy(monkeypatch):
    from kkleds import app

    opened = []
    monkeypatch.setattr(app, "_already_running", lambda port: True)
    monkeypatch.setattr(app.webbrowser, "open", opened.append)
    assert app.main(["--port", "9999"]) == 0
    assert opened == ["http://127.0.0.1:9999/"]
    assert app.main(["--background"]) == 0 and len(opened) == 1  # login start: no window


def test_autostart_command():
    from kkleds import autostart

    cmd = autostart.command()
    assert cmd.endswith("--background") and "kkleds.app" in cmd  # running from source


def test_icon():
    from kkleds.icon import make_icon

    img = make_icon(32)
    assert img.size == (32, 32) and img.getpixel((0, 0))[3] == 0  # transparent rounded corner
