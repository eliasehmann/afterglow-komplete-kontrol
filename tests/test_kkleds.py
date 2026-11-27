import io
import json

import pytest

from kkleds import colors, notes
from kkleds.cli import main
from kkleds.effects import EFFECTS
from kkleds.layout import S88, Frame


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
     ("#ff0000", 0x06), ("#ff8080", 0x07), ("#fff", 0x46), (12, 12)],
)
def test_parse(spec, expected):
    assert colors.parse(spec) == expected


@pytest.mark.parametrize("bad", ["chartreuse", "red:4", "red:loud", "0x80", "#12345"])
def test_parse_rejects(bad):
    with pytest.raises(ValueError):
        colors.parse(bad)


def test_rgb_round_trip():
    for value in range(4, 0x47):  # white:pastel (0x47) previews the same as white:2
        assert colors.from_rgb(*colors.to_rgb(value)) == value, hex(value)
    assert colors.from_rgb(0, 0, 0) == colors.OFF


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
    assert notes.select("C-1-A0", lo, hi) == [21]  # clipped to the keyboard


# ---- frames ----------------------------------------------------------------
def test_report_layout():
    f = Frame(S88).paint([21], colors.parse("red")).paint([108], colors.parse("blue"))
    report = f.to_report()
    assert len(report) == 89
    assert report[0] == 0x81
    assert report[1] == 0x06
    assert report[88] == colors.make("blue")


def test_scale_and_gradient():
    f_all = Frame(S88).select()
    f = Frame(S88).scale(f_all, 0, notes.scale("major"), 1, root_color=2, other=3)
    assert f.keys[f.index(60)] == 2  # C = root
    assert f.keys[f.index(62)] == 1  # D in scale
    assert f.keys[f.index(61)] == 3  # C# outside
    g = Frame(S88).gradient(f_all, [colors.parse("red"), colors.parse("blue")])
    assert g.keys[0] == colors.parse("red") and g.keys[-1] == colors.parse("blue")


def test_presets_load():
    import pathlib

    for path in pathlib.Path(__file__).parent.parent.glob("presets/*.json"):
        frame = Frame.from_preset_file(S88, path)
        assert len(frame.keys) == 88
        json.loads(path.read_text())


def test_effects_produce_frames():
    for name, effect in EFFECTS.items():
        gen = effect(S88, fps=30)
        for _ in range(60):
            keys = next(gen)
            assert len(keys) == 88 and all(0 <= k <= 0x7F for k in keys), name


# ---- CLI (dry run) -----------------------------------------------------------
@pytest.mark.parametrize(
    "argv",
    [
        ["fill", "red", "--dry-run"],
        ["set", "A0-B2=blue", "C=white", "--dry-run"],
        ["scale", "D", "dorian", "--dry-run"],
        ["gradient", "red", "yellow", "blue", "--dry-run"],
        ["piano", "--dry-run"],
        ["palette", "--dry-run"],
        ["animate", "rainbow", "--duration", "0.1", "--dry-run"],
        ["buttons", "play=on", "display=blue", "dis3=red", "29=dim", "--dry-run"],
        ["buttons", "--list"],
        ["strip", "rainbow", "--dry-run"],
        ["strip", "green", "red", "--bar", "60", "--dry-run"],
        ["preset", "presets/split-keyboard.json", "--dry-run"],
        ["animate", "chase", "--strip", "--duration", "0.1", "--dry-run"],
        ["raw", "81", "04", "08", "--dry-run"],
    ],
)
def test_cli_dry_run(argv, capsys):
    assert main(argv) == 0


def test_cli_bad_colour(capsys):
    assert main(["fill", "chartreuse", "--dry-run"]) == 2
    assert main(["buttons", "nonsense=on", "--dry-run"]) == 2


# ---- buttons / touch strip -----------------------------------------------------
from kkleds import buttons as btn  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(btn, "STATE_FILE", tmp_path / "state.json")


def test_button_names_and_aliases():
    assert btn.resolve("play") == [29]
    assert btn.resolve("fixedvel") == btn.resolve("fixed_vel") == [41]
    assert btn.resolve("dis1") == btn.resolve("display_1") == [2]
    assert btn.resolve("4dtop") == [11]
    assert btn.resolve("display") == list(range(2, 10))
    assert btn.resolve("strip") == list(range(44, 69))
    assert btn.resolve("strip_1,strip_25") == [44, 68]
    assert btn.resolve("play,rec") == [29, 30]
    with pytest.raises(ValueError):
        btn.resolve("octave_up")


def test_button_frame():
    f = btn.ButtonFrame().set("transport", btn.parse_value("on"))
    assert f.values[29] == colors.make("white", 2)
    f.strip([colors.parse("red"), colors.parse("blue")])
    assert f.values[44] == colors.parse("red") and f.values[68] == colors.parse("blue")
    f.strip_bar(0.4, colors.parse("green"))
    assert f.values[44:54] == [colors.parse("green")] * 10 and f.values[54] == colors.OFF
    assert len(f.values) == btn.REPORT_SIZE == 69


def test_button_state_round_trip():
    f = btn.ButtonFrame().set("mute", colors.parse("red"))
    btn.save_state(f)
    assert btn.load_state().values == f.values
