import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from kkleds import buttons as btn
from kkleds import colors, engine as engine_mod, profile
from kkleds.engine import Engine, EngineError
from kkleds.webui import make_handler

REG = profile.load_registry()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(btn, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(engine_mod, "USER_PRESETS", tmp_path / "presets")
    monkeypatch.setattr(engine_mod, "USER_SCENES", tmp_path / "scenes")


@pytest.fixture
def eng():
    e = Engine(REG, REG.get("s88-mk2"), dry_run=True)
    yield e
    e.close()


def tick(e):
    with e.lock:
        e._tick(time.monotonic())


# ---- engine ------------------------------------------------------------------
@pytest.mark.parametrize(
    "spec",
    [
        {"mode": "off"},
        {"mode": "solid", "color": "on"},
        {"mode": "gradient", "colors": ["red", "#0000ff"], "keys": "C3-C6"},
        {"mode": "scale", "root": "D", "scale": "dorian", "other": "plum:0"},
        {"mode": "piano", "white": "dim", "black": "blue"},
        {"mode": "layers", "layers": [{"fill": "off"}, {"keys": "A0-B2", "color": "dim"}]},
        {"mode": "custom", "keys": ["red", "#00ff00", None]},
        {"mode": "preset", "name": "split-keyboard"},
        {"mode": "animate", "effect": "sparkle", "speed": 2, "fps": 45, "color": "cyan"},
    ],
)
def test_modes(eng, spec):
    eng.set_mode(spec)
    tick(eng)
    st = eng.state()
    assert st["mode"]["mode"] == spec["mode"]
    assert len(st["keys"]) == 88 and all(k.startswith("#") and len(k) == 7 for k in st["keys"])


def test_solid_and_custom_values(eng):
    eng.set_mode({"mode": "solid", "color": "red"})
    assert eng.state()["keys"][0] == "#ff0000"
    eng.set_mode({"mode": "custom", "keys": ["blue"]})
    st = eng.state()
    assert st["keys"][0] == "#0000ff" and st["keys"][1] == "#000000"


def test_animation_advances(eng):
    eng.set_mode({"mode": "animate", "effect": "rainbow", "speed": 3, "fps": 30})
    t0 = time.monotonic()
    with eng.lock:
        eng._tick(t0)
    first = eng.state()["keys"]
    with eng.lock:
        for k in range(1, 11):  # one tick per frame (animations render at their own frame rate)
            eng._tick(t0 + k / 30)
    assert eng.state()["keys"] != first


@pytest.mark.parametrize(
    "spec",
    [{"mode": "dance"}, {"mode": "solid", "color": "chartreuse"}, {"mode": "animate", "effect": "nope"},
     {"mode": "preset", "name": "../../etc/passwd"}, {"mode": "preset", "name": "missing"},
     {"mode": "scale", "root": "H"}],
)
def test_bad_modes_rejected_and_state_kept(eng, spec):
    eng.set_mode({"mode": "solid", "color": "green"})
    with pytest.raises(ValueError):
        eng.set_mode(spec)
    assert eng.state()["mode"] == {"mode": "solid", "color": "green"}


def test_buttons_and_strip(eng):
    eng.set_buttons({"assign": {"transport": "on", "mute": "red"}})
    b = eng.state()["buttons"]
    assert b[29] == "#ffffff"  # play = on (white:2)
    assert b[0] == "#ff0000"
    eng.set_buttons({"strip": {"mode": "bar", "percent": 40, "color": "green"}})
    b = eng.state()["buttons"]
    assert b[44] != "#000000" and b[68] == "#000000"
    eng.set_mode({"mode": "solid", "color": "blue"})
    eng.set_buttons({"strip": {"mode": "mirror"}})
    assert eng.state()["buttons"][44] == "#0000ff"
    eng.set_buttons({"reset": True})
    assert set(eng.state()["buttons"]) == {"#000000"}
    with pytest.raises(ValueError):
        eng.set_buttons({"strip": {"mode": "laser"}})


def test_save_preset_round_trip(eng):
    eng.set_mode({"mode": "layers", "layers": [{"keys": "A0-B2", "color": "blue"}, {"keys": "C", "color": "white"}]})
    eng.set_buttons({"assign": {"play": "on"}})
    path = eng.save_preset("my look")
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["buttons"]["play"] == "white:2"
    before = eng.state()["keys"]
    eng.set_mode({"mode": "off"})
    eng.set_mode({"mode": "preset", "name": "my look"})
    assert eng.state()["keys"] == before
    assert any(p["name"] == "my look" and p["source"] == "saved" for p in engine_mod.list_presets())
    with pytest.raises(EngineError):
        eng.save_preset("../evil")


def test_paint_your_own_look(eng):
    """What the paint editor does: per-key colours, strip dots, buttons, save, reload, delete."""
    eng.set_mode({"mode": "scene", "scene": "fire"})
    tick(eng)
    seen = eng.state()["keys"]
    eng.set_mode({"mode": "custom", "keys": seen})  # start from what's on screen
    assert eng.state()["keys"] == seen
    keys = ["off"] * 88
    keys[0], keys[1], keys[87] = "red:2", "blue:0", "#00ff00"
    eng.set_mode({"mode": "custom", "keys": keys})
    eng.set_buttons({"assign": {"strip_1": "yellow", "strip_25": "magenta:1", "play": "on", "mute": "cyan"}})
    st = eng.state()
    assert st["keys"][:2] == ["#ff0000", "#000055"] and st["keys"][87] == "#00ff00"
    assert st["buttons"][44] == "#ffff00" and st["buttons"][29] == "#ffffff"
    eng.save_preset("My look")
    eng.all_off()
    assert set(eng.state()["keys"]) == {"#000000"}
    eng.set_mode({"mode": "preset", "name": "My look"})
    st2 = eng.state()
    assert st2["keys"] == st["keys"] and st2["buttons"] == st["buttons"]
    eng.delete_preset("My look")
    assert not any(p["name"] == "My look" for p in engine_mod.list_presets())
    with pytest.raises(EngineError):
        eng.delete_preset("split-keyboard")  # built-in looks can't be deleted


def test_panel_drawing(eng):
    panel = eng.info()["panel"]
    blocks = [block for column in panel["columns"] for block in column] + panel["side"]
    items = []
    for block in blocks:
        if block["type"] == "buttons":
            items += [it for row in block["rows"] for it in row]
        elif block["type"] == "encoder":
            items += [block[d] for d in ("up", "down", "left", "right")]
    slots = [it["slot"] for it in items if it["slot"] is not None]
    b = REG.get("s88-mk2").buttons
    assert sorted(slots) == b.groups["all"]  # each of the 42 buttons is drawn exactly once
    assert {it["label"] for it in items if it["slot"] is None} == {"OCT -", "OCT +"}  # drawn, not controllable
    assert [blk["type"] for blk in panel["side"]] == ["buttons", "wheels", "strip"]  # wheel module left of the keys
    assert eng.info()["panel"] is not None and Engine(REG, REG.get("s88-mk1"), dry_run=True).info()["panel"] is None


def test_white_only_buttons_preview_as_white(eng):
    """Single-colour buttons only light white: the UI must show them that way."""
    eng.set_buttons({"assign": {"play": "red:2", "rec": "blue:0", "stop": "red:pastel", "mute": "red:2", "strip_1": "red:1"}})
    b = eng.state()["buttons"]
    assert b[29] == "#ffffff"  # play: bright red -> bright white
    assert b[30] == "#555555"  # rec: dim blue -> dim white
    assert b[31] == "#ffffff"  # stop: pastel -> bright white
    assert b[0] == "#ff0000"   # mute is a colour LED
    assert b[44] == "#aa0000"  # strip dots are colour LEDs


def test_panel_validation():
    data = {"product_id": 1, "buttons": {"report": 1, "size": 4, "leds": {"go": 0}},
            "panel": {"columns": [[{"type": "buttons", "rows": [["go", "stop", "#oct"]]}, {"type": "dial"}]]}}
    problems = profile.validate(profile.load_data(data)[0])
    assert any("unknown LED 'stop'" in p for p in problems)
    assert any("'dial'" in p for p in problems)
    assert not any("oct" in p for p in problems)


def test_info(eng):
    info = eng.info()
    assert info["profile"]["key"] == "s88-mk2"
    assert info["keys"]["count"] == 88 and len(info["keys"]["black"]) == 88
    assert len(info["palette"]) == 17 and len(info["buttons"]["strip"]) == 25
    assert {"display", "transport", "all"} <= set(info["buttons"]["groups"])


def test_profile_without_buttons(eng):
    eng.connect("s88-mk1")
    assert eng.info()["buttons"] is None
    eng.set_mode({"mode": "solid", "color": "#123456"})
    assert eng.state()["keys"][0] == "#123456"  # RGB device keeps exact colour
    with pytest.raises(ValueError):
        eng.set_buttons({"assign": {"play": "on"}})


def test_loop_runs_in_background(eng):
    eng.set_mode({"mode": "animate", "effect": "chase", "speed": 4, "fps": 60})
    eng.start()
    a = eng.state()["keys"]
    time.sleep(0.25)
    assert eng.state()["keys"] != a


# ---- scenes ------------------------------------------------------------------
from kkleds import scenes  # noqa: E402


@pytest.mark.parametrize("scene_id", list(scenes.SCENES))
@pytest.mark.parametrize("model", ["s88-mk2", "s49-mk2", "s88-mk1"])
def test_every_scene_renders(scene_id, model):
    prof = REG.get(model)
    sc = scenes.make(scene_id, seed=1)
    lit_frames = 0
    for step in range(90):  # 3 seconds at 30 fps
        sc.update(step / 30, 1 / 30)
        keys, buttons = scenes.render(sc, prof, scenes.brightness_factor("high"))
        assert len(keys) == prof.key_count
        prof.keys_report(keys)  # encodes for the device without error
        if prof.buttons:
            assert len(buttons) == prof.buttons.size
            prof.buttons_report(buttons)
            assert all(colors.is_off(buttons[s]) for s in prof.buttons.groups["mono"])  # white buttons stay dark
        else:
            assert buttons is None
        lit_frames += any(not colors.is_off(colors.to_palette(k)) for k in keys)
    assert lit_frames > 45, f"{scene_id} is mostly dark on {model}"


def test_scene_brightness_caps_palette_level():
    sc = scenes.make("rainbow", seed=1)
    sc.update(0, 0)
    for level, cap in (("low", 0), ("medium", 1), ("high", 2)):
        keys, _ = scenes.render(sc, REG.get("s88-mk2"), scenes.brightness_factor(level))
        assert max(colors.split(colors.to_palette(k))[1] for k in keys) == cap


def test_scene_mode_in_engine(eng):
    eng.set_mode({"mode": "scene", "scene": "fire", "speed": 1.5, "brightness": "medium"})
    for _ in range(5):
        tick(eng)
        time.sleep(0.01)
    st = eng.state()
    assert st["mode"] == {"mode": "scene", "scene": "fire", "speed": 1.5, "brightness": "medium"}
    assert any(k != "#000000" for k in st["keys"])
    assert any(st["buttons"][s] != "#000000" for s in range(44, 69))  # strip follows the scene
    obj = eng._scene["obj"]
    eng.set_mode({"mode": "scene", "scene": "fire", "speed": 0.5, "brightness": "low"})
    assert eng._scene["obj"] is obj  # retune without restarting
    eng.set_buttons({"assign": {"play": "on"}})
    eng.set_mode({"mode": "off"})
    assert eng.state()["buttons"][29] == "#ffffff"  # saved buttons come back after the scene
    with pytest.raises(ValueError):
        eng.set_mode({"mode": "scene", "scene": "volcano"})
    with pytest.raises(ValueError):
        eng.set_mode({"mode": "scene", "scene": "fire", "brightness": "blinding"})


def test_dithering_smooths_fades():
    """A brightness between two palette levels lights a mix of both, in proportion."""
    prof = REG.get("s88-mk2")

    class Flat(scenes.Scene):
        level = 0.0

        def sample(self, x, w, layer):
            return (0.0, 0.0, self.level)

    sc = Flat()
    shares = []
    for v in (0.2, 0.4, 0.5, 0.6, 0.8, 1.0):
        sc.level = v
        keys, _ = scenes.render(sc, prof)
        levels = [colors.split(k)[1] if k else -1 for k in keys]
        assert len(set(levels)) <= 2  # neighbouring steps only
        shares.append(sum(l + 1 for l in levels) / len(levels))  # average step, 0 = off .. 3 = bright
    assert shares == sorted(shares)  # strictly follows the fade
    assert abs(shares[2] - 1.5) < 0.1  # v = 0.5 is half way between dim and medium


def test_fire_and_lava_stay_warm():
    """No pastel/white tints in Fire, no pink/purple in Lava."""
    prof = REG.get("s88-mk2")
    for scene_id, banned in (("fire", {"white", "magenta", "fuchsia", "purple"}), ("lava", {"white", "magenta", "fuchsia", "purple", "violet"})):
        sc = scenes.make(scene_id, seed=3)
        for step in range(300):
            sc.update(step / 30, 1 / 30)
            keys, buttons = scenes.render(sc, prof)
            for v in keys + [b for b in buttons if b]:
                hue, level = colors.split(v)
                assert hue not in banned and level != colors.PASTEL, (scene_id, colors.describe(v))


def test_custom_scene(eng):
    for motion in scenes.Custom.MOTIONS:
        eng.set_mode({"mode": "scene", "scene": "custom", "colors": ["red", "#00ff00", "blue"], "motion": motion})
        for _ in range(3):
            tick(eng)
            time.sleep(0.01)
        assert eng.state()["mode"]["motion"] == motion
    obj = eng._scene["obj"]
    eng.set_mode({"mode": "scene", "scene": "custom", "colors": ["yellow"], "motion": "still"})
    assert eng._scene["obj"] is obj  # edited live, not restarted
    tick(eng)
    assert set(eng.state()["keys"]) == {"#ffff00"}
    with pytest.raises(ValueError):
        eng.set_mode({"mode": "scene", "scene": "custom", "colors": [], "motion": "flow"})
    with pytest.raises(ValueError):
        eng.set_mode({"mode": "scene", "scene": "custom", "colors": ["red"], "motion": "spin"})


def test_save_and_delete_custom_scene(eng):
    eng.save_scene("Embers", ["red", "orange"], "flicker")
    saved = eng.info()["custom_scenes"]
    assert saved == [{"name": "Embers", "colors": ["red", "orange"], "motion": "flicker", "swatch": ["#ff0000", "#ff4600"]}]
    eng.delete_scene("Embers")
    assert eng.info()["custom_scenes"] == []
    for bad in ("../x", ""):
        with pytest.raises(EngineError):
            eng.save_scene(bad, ["red"], "flow")
    with pytest.raises(EngineError):
        eng.delete_scene("Embers")


def test_info_lists_scenes_and_preset_swatches(eng):
    info = eng.info()
    assert {s["id"] for s in info["scenes"]} == set(scenes.SCENES)
    split = next(p for p in info["presets"] if p["name"] == "split-keyboard")
    assert len(split["swatch"]) == Engine.PREVIEW_LEDS
    assert split["swatch"][0] == "#0000ff" and split["swatch"][-1] == "#ff4600"  # real key colours: bass blue … lead orange


# ---- web server --------------------------------------------------------------
@pytest.fixture
def server(eng):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(eng))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def call(url, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


API = {"X-KKLeds": "1", "Content-Type": "application/json"}


def test_web_pages_and_api(server):
    code, body = call(server + "/")
    assert code == 200 and b"<title>Afterglow</title>" in body
    code, body = call(server + "/api/info")
    assert code == 200 and json.loads(body)["profile"]["key"] == "s88-mk2"
    code, body = call(server + "/api/mode", {"mode": "solid", "color": "red"}, API)
    assert code == 200 and json.loads(body)["state"]["keys"][0] == "#ff0000"
    code, body = call(server + "/api/buttons", {"assign": {"display": "blue"}}, API)
    assert code == 200
    code, body = call(server + "/api/mode", {"mode": "solid", "color": "chartreuse"}, API)
    assert code == 400 and "unknown colour" in json.loads(body)["error"]
    code, body = call(server + "/api/scenes/save", {"name": "Mine", "colors": ["cyan"], "motion": "pulse"}, API)
    assert code == 200
    code, body = call(server + "/api/scenes/delete", {"name": "Mine"}, API)
    assert code == 200


def test_page_script_uses_single_argument_selector_helpers():
    """`$`/`$$` in the page only search the whole document; a second argument is silently ignored."""
    import re
    from kkleds.webui import INDEX

    script = INDEX.read_text(encoding="utf-8")
    assert not re.findall(r"\$\$?\(\s*[\"'`][^\"'`]*[\"'`]\s*,", script)


def test_web_rejects_cross_site_requests(server):
    # no custom header: what a cross-site form / simple fetch would send
    code, _ = call(server + "/api/mode", {"mode": "off"}, {"Content-Type": "text/plain"})
    assert code == 403
    # DNS-rebinding style Host header
    code, _ = call(server + "/api/state", headers={"Host": "evil.example:8765"})
    assert code == 403
    code, _ = call(server + "/api/mode", {"mode": "off"}, {**API, "Host": "evil.example"})
    assert code == 403
