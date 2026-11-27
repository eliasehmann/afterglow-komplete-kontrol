import json
import pathlib

import pytest

from kkleds import buttons as btn
from kkleds import colors, notes, profile
from kkleds.cli import main
from kkleds.colors import Rgb
from kkleds.effects import EFFECTS
from kkleds.encodings import MonoEncoding, PaletteEncoding, RgbEncoding, from_spec
from kkleds.layout import Frame

ROOT = pathlib.Path(__file__).parent.parent
REG = profile.load_registry()
S88 = REG.get("s88-mk2")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Never touch the real ~/.kkleds state or user profiles."""
    monkeypatch.setattr(btn, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(profile, "USER_DIR", tmp_path / "profiles")


# ---- colours ---------------------------------------------------------------
def test_make_and_split():
    assert colors.make("red") == 0x06  # bright, saturated (confirmed on hardware)
    assert colors.make("red", 3) == 0x07  # pastel / pink
    assert colors.make("red", 0) == 0x04
    assert colors.make("blue", 0) == 0x2C  # matches SynthesiaKontrol's blue
    assert colors.make("green", 1) == 0x1D
    assert colors.make("white", 3) == 0x47
    for value in range(4, 0x48):
        hue, b = colors.split(value)
        assert colors.make(hue, b) == value


@pytest.mark.parametrize(
    "spec, expected",
    [("off", 0), ("red", 0x06), ("red:pastel", 0x07), ("red:dim", 0x04), ("Blue:1", colors.make("blue", 1)),
     ("light-orange", colors.make("light_orange")), ("pink", colors.make("fuchsia")), ("0x2b", 0x2B), ("43", 43),
     ("#ff0000", Rgb(255, 0, 0)), ("#fff", Rgb(255, 255, 255)), ([1, 2, 3], Rgb(1, 2, 3)), (12, 12)],
)
def test_parse(spec, expected):
    assert colors.parse(spec) == expected


@pytest.mark.parametrize("bad", ["chartreuse", "red:4", "red:loud", "0x80", "#12345", [1, 2]])
def test_parse_rejects(bad):
    with pytest.raises(ValueError):
        colors.parse(bad)


def test_rgb_palette_round_trip():
    for value in range(4, 0x47):  # white:pastel (0x47) previews the same as white:2
        assert colors.from_rgb(*colors.to_rgb(value)) == value, hex(value)
    assert colors.from_rgb(0, 0, 0) == colors.OFF
    assert colors.to_palette(colors.parse("#ff0000")) == 0x06
    assert colors.to_palette(colors.parse("#ff8080")) == 0x07


def test_lerp_keeps_rgb_when_given_rgb():
    assert isinstance(colors.lerp(colors.parse("red"), colors.parse("blue"), 0.5), int)
    assert isinstance(colors.lerp(Rgb(255, 0, 0), colors.parse("blue"), 0.5), Rgb)


# ---- encodings -------------------------------------------------------------
def test_encodings():
    assert PaletteEncoding().encode(colors.parse("red")) == b"\x06"
    assert PaletteEncoding().encode(Rgb(255, 0, 0)) == b"\x06"
    assert RgbEncoding().encode(colors.parse("red")) == bytes([255, 0, 0])
    assert RgbEncoding("grb", 127).encode(Rgb(255, 128, 0)) == bytes([64, 127, 0])
    assert MonoEncoding().encode(colors.parse("white:2")) == bytes([127])
    assert MonoEncoding().encode(colors.OFF) == bytes([0])
    assert from_spec({"type": "rgb", "order": "bgr"}).order == "bgr"
    with pytest.raises(ValueError):
        from_spec("hsv")
    with pytest.raises(ValueError):
        RgbEncoding("rrg")


# ---- profiles --------------------------------------------------------------
def test_builtin_profiles_load_and_validate():
    keys = [p.key for p in REG]
    assert {"s49-mk2", "s61-mk2", "s88-mk2", "s25-mk1", "s49-mk1", "s61-mk1", "s88-mk1"} <= set(keys)
    assert len(keys) == len(set(keys))
    usb = [(p.vendor_id, p.product_id) for p in REG]
    assert len(usb) == len(set(usb))
    for p in REG:
        assert validate_ok(p), (p.key, profile.validate(p))


def validate_ok(p):
    return profile.validate(p) == []


def test_s88_mk2_profile_matches_hardware_findings():
    assert S88.usb_id == "17CC:1630" and S88.status == "verified"
    assert S88.init == (bytes([0xA0, 0x00, 0x00]),)
    assert (S88.key_count, S88.lowest_note, S88.highest_note) == (88, 21, 108)
    b = S88.buttons
    assert (b.report, b.size) == (0x80, 69)
    assert b.strip == tuple(range(44, 69))
    assert b.resolve("play") == [29] and b.resolve("fixed_vel") == [41]
    assert b.kind(b.leds["mute"]) == "colour" and b.kind(b.leds["play"]) == "single colour"
    assert b.groups["all"] == list(range(42))


def test_variant_inherits_and_overrides():
    s49 = REG.get("s49-mk2")
    assert s49.key_count == 49 and s49.buttons.size == 69 and s49.status == "unverified"
    mk1 = REG.get("s88-mk1")
    assert mk1.light_guide.report == 0x82 and mk1.light_guide.encoding.bytes_per_led == 3
    assert mk1.buttons is None


def test_registry_lookup():
    assert REG.get("S88-MK2") is S88
    assert REG.get("s88mk2") is S88
    assert REG.by_usb(0x17CC, 0x1630) is S88
    with pytest.raises(profile.ProfileError):
        REG.get("s88")  # ambiguous: mk1 and mk2
    with pytest.raises(profile.ProfileError):
        REG.get("nope")


def test_reports():
    f = Frame(S88).paint([21], colors.parse("red")).paint([108], colors.parse("blue"))
    report = f.to_report()
    assert len(report) == 89 and report[0] == 0x81
    assert report[1] == 0x06 and report[88] == colors.make("blue")
    mk1 = Frame(REG.get("s25-mk1")).paint([48], colors.parse("red"))
    report = mk1.to_report()
    assert len(report) == 1 + 25 * 3 and report[0] == 0x82 and report[1:4] == bytes([255, 0, 0])
    assert len(S88.buttons_report([])) == 70


def test_custom_profile_file(tmp_path):
    data = {
        "id": "test-family",
        "name": "Test Keyboard",
        "vendor_id": "0x1234",
        "light_guide": {"report": "0x90", "encoding": {"type": "rgb", "order": "grb"}},
        "buttons": {
            "report": "0x91", "size": 8, "encoding": "mono",
            "leds": {"go": 0, "halt": 1}, "strip": {"first": 4, "length": 4},
            "groups": {"transport": ["go", "halt"]}, "color": ["strip"],
        },
        "variants": {"test-32": {"product_id": "0x0032", "light_guide": {"keys": 32, "lowest_note": 41}}},
    }
    path = tmp_path / "test.json"
    path.write_text(json.dumps(data))
    reg = profile.load_registry([path])
    p = reg.get("test-32")
    assert p.usb_id == "1234:0032" and p.key_count == 32
    assert p.buttons.resolve("transport") == [0, 1]
    assert p.buttons.resolve("strip_4") == [7]
    assert p.buttons.groups["mono"] == [0, 1]
    assert p.buttons_report([colors.parse("white")]) == bytes([0x91, 127, 0, 0, 0, 0, 0, 0, 0])
    assert profile.validate(p) == []


@pytest.mark.parametrize(
    "broken, message",
    [
        ({"product_id": "0x1"}, "neither"),
        ({"product_id": "zz", "light_guide": {"report": 1, "keys": 1, "lowest_note": 1}}, "expected a number"),
        ({"product_id": 1, "light_guide": {"report": 1, "keys": 5}}, "lowest_note is missing"),
        ({"product_id": 1, "buttons": {"report": 1, "size": 2, "leds": {"a": 0}, "aliases": {"b": "c"}}}, "unknown LED"),
        ({"product_id": 1, "status": "maybe", "light_guide": {"report": 1, "keys": 1, "lowest_note": 1}}, "status"),
    ],
)
def test_bad_profiles_are_reported(broken, message):
    try:
        problems = [problem for p in profile.load_data(broken) for problem in profile.validate(p)]
        text = " ".join(problems)
    except profile.ProfileError as e:
        text = str(e)
    assert message in text


# ---- notes -----------------------------------------------------------------
def test_note_names():
    assert notes.to_midi("C4") == 60
    assert notes.to_midi("A0") == 21
    assert notes.to_midi("C8") == 108
    assert notes.to_midi("Bb3") == 58
    assert notes.to_midi("C-1") == 0
    assert notes.name(61) == "C#4"
    assert notes.pitch_class("F#") == 6


def test_select():
    lo, hi = S88.lowest_note, S88.highest_note
    assert len(notes.select("all", lo, hi)) == 88
    assert len(notes.select("white", lo, hi)) == 52
    assert len(notes.select("black", lo, hi)) == 36
    assert notes.select("C4-E4", lo, hi) == [60, 61, 62, 63, 64]
    assert notes.select("C4..C#4", lo, hi) == [60, 61]
    assert notes.select("60-61", lo, hi) == [60, 61]
    assert notes.select("C", lo, hi) == [24, 36, 48, 60, 72, 84, 96, 108]
    assert notes.select("C4,E4,G4", lo, hi) == [60, 64, 67]
    assert notes.select("C-1-A0", lo, hi) == [21]


# ---- frames ----------------------------------------------------------------
def test_scale_and_gradient():
    f_all = Frame(S88).select()
    f = Frame(S88).scale(f_all, 0, notes.scale("major"), 1, root_color=2, other=3)
    assert f.keys[f.index(60)] == 2
    assert f.keys[f.index(62)] == 1
    assert f.keys[f.index(61)] == 3
    g = Frame(S88).gradient(f_all, [colors.parse("red"), colors.parse("blue")])
    assert g.keys[0] == colors.parse("red") and g.keys[-1] == colors.parse("blue")


def test_presets_load():
    paths = list(ROOT.glob("src/kkleds/presets/*.json"))
    assert len(paths) >= 3
    for path in paths:
        assert len(Frame.from_preset_file(S88, path).keys) == 88
        data = json.loads(path.read_text())
        if "buttons" in data:
            btn.ButtonFrame(S88.buttons).apply(data["buttons"])


def test_frame_needs_light_guide():
    no_lg = profile.load_data({"product_id": 1, "buttons": {"report": 1, "size": 2}})[0]
    with pytest.raises(profile.ProfileError):
        Frame(no_lg)


def test_effects_produce_frames():
    for name, effect in EFFECTS.items():
        gen = effect(S88, fps=30)
        for _ in range(60):
            keys = next(gen)
            assert len(keys) == 88 and all(0 <= k <= 0x7F for k in keys), name


# ---- buttons ---------------------------------------------------------------
def test_button_frame():
    f = btn.ButtonFrame(S88.buttons).set("transport", btn.parse_value("on"))
    assert f.values[29] == colors.make("white", 2)
    f.strip([colors.parse("red"), colors.parse("blue")])
    assert f.values[44] == colors.parse("red") and f.values[68] == colors.parse("blue")
    f.strip_bar(0.4, colors.parse("green"))
    assert f.values[44:54] == [colors.parse("green")] * 10 and f.values[54] == colors.OFF
    with pytest.raises(ValueError):
        f.set("octave_up", 1)


def test_button_state_round_trip():
    f = btn.ButtonFrame(S88.buttons).set("mute", colors.parse("red")).set("solo", Rgb(1, 2, 3))
    btn.save_state(S88, f)
    assert btn.load_state(S88).values == f.values


# ---- CLI (dry run) ---------------------------------------------------------
@pytest.mark.parametrize(
    "argv",
    [
        ["fill", "red", "--dry-run"],
        ["fill", "#ff8800", "--dry-run", "--model", "s88-mk1"],
        ["set", "A0-B2=blue", "C=white", "--dry-run"],
        ["scale", "D", "dorian", "--dry-run", "--model", "s61-mk2"],
        ["gradient", "red", "yellow", "blue", "--dry-run"],
        ["piano", "--dry-run"],
        ["palette", "--dry-run"],
        ["animate", "rainbow", "--duration", "0.1", "--dry-run"],
        ["animate", "chase", "--strip", "--duration", "0.1", "--dry-run"],
        ["buttons", "play=on", "display=blue", "dis3=red", "29=dim", "--dry-run"],
        ["buttons", "--list", "--model", "s88-mk2"],
        ["strip", "rainbow", "--dry-run"],
        ["strip", "green", "red", "--bar", "60", "--dry-run"],
        ["preset", "split-keyboard", "--dry-run"],
        ["preset", str(ROOT / "src" / "kkleds" / "presets" / "sunset.json"), "--dry-run"],
        ["preset", "--list"],
        ["raw", "81", "04", "08", "--dry-run"],
        ["scene", "--list"],
        ["scene", "storm", "--duration", "0.2", "--dry-run"],
        ["scene", "aurora", "--duration", "0.1", "--brightness", "low", "--dry-run", "--model", "s88-mk1"],
        ["devices"],
        ["colors"],
        ["profile-check", str(ROOT / "src" / "kkleds" / "profiles" / "komplete-kontrol-s-mk2.json")],
    ],
)
def test_cli_ok(argv, capsys):
    assert main(argv) == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["fill", "chartreuse", "--dry-run"],
        ["buttons", "nonsense=on", "--dry-run"],
        ["buttons", "play=on", "--dry-run", "--model", "s88-mk1"],  # MK1 profile has no buttons
        ["fill", "red", "--dry-run", "--model", "s88"],  # ambiguous
    ],
)
def test_cli_errors(argv, capsys):
    assert main(argv) == 2


def test_profile_new_round_trip(tmp_path, capsys):
    out = tmp_path / "a49.json"
    assert main(["profile-new", "--pid", "0x1730", "--key", "a49-test", "--keys", "49", "--out", str(out)]) == 0
    assert main(["profile-check", str(out)]) == 0
    assert main(["fill", "red", "--dry-run", "--profile", str(out), "--model", "a49-test"]) == 0
