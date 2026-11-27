"""kkleds - command line control of the Komplete Kontrol S88 MK2 light guide."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import buttons as btn
from . import colors, notes
from .device import DeviceError, DryRunDevice, HidDevice, _path_str, list_interfaces
from .effects import EFFECTS
from .layout import MODELS, MODELS_BY_PID, S88, Frame


# --------------------------------------------------------------------------- helpers
def open_device(args):
    model = MODELS[args.model] if args.model else None
    if args.dry_run:
        return DryRunDevice(model or S88)
    return HidDevice.open(model=model, path=args.path)


def push(dev, frame: Frame, hold: bool) -> None:
    dev.write_keys(frame.keys)
    if hold:
        print("\nHolding - re-sending every 0.5 s. Ctrl+C to stop.", file=sys.stderr)
        try:
            while True:
                time.sleep(0.5)
                dev.write_keys(frame.keys)
        except KeyboardInterrupt:
            pass


def base_frame(model, spec: Optional[str]) -> Frame:
    """--base accepts a colour or a preset JSON file."""
    if not spec:
        return Frame(model)
    if spec.lower().endswith(".json") or Path(spec).is_file():
        return Frame.from_preset_file(model, spec)
    return Frame(model, colors.parse(spec))


def send_buttons(dev, frame: "btn.ButtonFrame", args) -> None:
    dev.write_buttons(frame.values)
    if not args.dry_run:
        btn.save_state(frame)


def hold_buttons(dev, frame: "btn.ButtonFrame") -> None:
    print("Holding - re-sending every 0.5 s. Ctrl+C to stop.", file=sys.stderr)
    try:
        while True:
            time.sleep(0.5)
            dev.write_buttons(frame.values)
    except KeyboardInterrupt:
        pass


def strip_from_keys(keys: List[int]) -> List[int]:
    """Sample a light guide frame down to the 25 touch strip dots."""
    n = len(keys)
    return [keys[min(n - 1, round((i + 0.5) * n / btn.STRIP_LEN))] for i in range(btn.STRIP_LEN)]


def split_assignment(text: str):
    key, sep, value = text.rpartition("=")
    if not sep or not key:
        raise ValueError(f"expected KEYS=COLOUR, got {text!r}")
    return key, value


# --------------------------------------------------------------------------- commands
def cmd_list(args) -> None:
    infos = list_interfaces()
    if not infos:
        print("No Native Instruments HID devices found.")
        return
    for d in infos:
        model = MODELS_BY_PID.get(d["product_id"])
        print(
            f"{d.get('product_string') or '?'}  "
            f"(VID 0x{d['vendor_id']:04X} PID 0x{d['product_id']:04X}"
            f"{' = ' + model.name if model else ''})\n"
            f"  interface {d.get('interface_number')}  usage_page 0x{d.get('usage_page', 0):04X}  "
            f"usage 0x{d.get('usage', 0):04X}\n"
            f"  path {_path_str(d['path'])}"
        )


def cmd_off(args) -> None:
    with open_device(args) as dev:
        dev.write_keys([colors.OFF] * dev.model.key_count)


def cmd_fill(args) -> None:
    with open_device(args) as dev:
        push(dev, Frame(dev.model, colors.parse(args.color)), args.hold)


def cmd_set(args) -> None:
    with open_device(args) as dev:
        frame = base_frame(dev.model, args.base)
        for item in args.assignments:
            keys, color = split_assignment(item)
            frame.paint(frame.select(keys), colors.parse(color))
        push(dev, frame, args.hold)


def cmd_scale(args) -> None:
    with open_device(args) as dev:
        frame = base_frame(dev.model, args.base)
        frame.scale(
            frame.select(args.keys),
            notes.pitch_class(args.root),
            notes.scale(args.kind),
            colors.parse(args.color),
            colors.parse(args.root_color) if args.root_color else None,
            colors.parse(args.other) if args.other else None,
        )
        push(dev, frame, args.hold)


def cmd_gradient(args) -> None:
    with open_device(args) as dev:
        frame = base_frame(dev.model, args.base)
        frame.gradient(frame.select(args.keys), [colors.parse(c) for c in args.colors])
        push(dev, frame, args.hold)


def cmd_piano(args) -> None:
    with open_device(args) as dev:
        frame = Frame(dev.model)
        frame.paint(frame.select("white"), colors.parse(args.white))
        frame.paint(frame.select("black"), colors.parse(args.black))
        push(dev, frame, args.hold)


def cmd_preset(args) -> None:
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    with open_device(args) as dev:
        frame = Frame(dev.model).apply_preset(data)
        if isinstance(data, dict) and "buttons" in data:
            send_buttons(dev, btn.ButtonFrame().apply(data["buttons"]), args)
        push(dev, frame, args.hold)


def cmd_animate(args) -> None:
    effect = EFFECTS[args.effect]
    color = colors.parse(args.color) if args.color else None
    with open_device(args) as dev:
        frames = effect(dev.model, fps=args.fps, speed=args.speed, color=color)
        interval = 1.0 / args.fps
        deadline = time.monotonic() + args.duration if args.duration else None
        saved_buttons = btn.load_state()
        strip_frame = saved_buttons.values[:]
        print(f"Running {args.effect} at {args.fps} fps. Ctrl+C to stop.", file=sys.stderr)
        next_tick = time.monotonic()
        try:
            for keys in frames:
                dev.write_keys(keys)
                if args.strip:
                    strip_frame[btn.STRIP_FIRST:btn.STRIP_FIRST + btn.STRIP_LEN] = strip_from_keys(keys)
                    dev.write_buttons(strip_frame)
                if deadline and time.monotonic() >= deadline:
                    break
                next_tick += interval
                time.sleep(max(0.0, next_tick - time.monotonic()))
        except KeyboardInterrupt:
            pass
        if not args.keep:
            dev.write_keys([colors.OFF] * dev.model.key_count)
            if args.strip:
                dev.write_buttons(saved_buttons.values)


VELOCITY_HUES = ("blue", "turquoise", "cyan", "mint", "green", "lime", "yellow",
                 "warm_yellow", "light_orange", "orange", "red")


def _pick_midi_port(mido, wanted: Optional[str]) -> str:
    ports = mido.get_input_names()
    if wanted:
        for p in ports:
            if wanted.lower() in p.lower():
                return p
        raise DeviceError(f"No MIDI input matching {wanted!r}. Available: {ports}")
    for test in (lambda p: "mk2 midi" in p, lambda p: "kontrol" in p and "ext" not in p):
        for p in ports:
            if test(p.lower()):
                return p
    raise DeviceError(f"Could not find the keyboard's MIDI input; pass --port. Available: {ports}")


def cmd_follow(args) -> None:
    try:
        import mido
    except ImportError as e:
        raise DeviceError("follow needs MIDI support: pip install -e .[midi]") from e

    if args.list_ports:
        for p in mido.get_input_names():
            print(p)
        return

    port_name = _pick_midi_port(mido, args.port)
    highlight = colors.parse(args.color)
    with open_device(args) as dev:
        base = base_frame(dev.model, args.base)
        frame = base.copy()
        down = set()       # notes physically held
        sustained = set()  # notes released while the pedal was down
        pedal = False
        dev.write_keys(frame.keys)
        meter = btn.load_state()
        meter_colors = colors.gradient([colors.make(c) for c in ("green", "yellow", "red")], btn.STRIP_LEN)

        def show_meter(velocity: int) -> None:
            lit = round(velocity / 127 * btn.STRIP_LEN)
            for n, slot in enumerate(btn.STRIP):
                meter.values[slot] = meter_colors[n] if n < lit else colors.OFF
            dev.write_buttons(meter.values)

        print(f"Following {port_name!r}. Ctrl+C to stop.", file=sys.stderr)

        def light(note: int, color: Optional[int]) -> None:
            i = frame.index(note - args.shift)
            if i is not None:
                frame.keys[i] = base.keys[i] if color is None else color

        try:
            with mido.open_input(port_name) as port:
                last_send = time.monotonic()
                while True:
                    changed = False
                    for msg in port.iter_pending():
                        if msg.type == "note_on" and msg.velocity > 0:
                            down.add(msg.note)
                            sustained.discard(msg.note)
                            if args.velocity:
                                hue = VELOCITY_HUES[msg.velocity * len(VELOCITY_HUES) // 128]
                                light(msg.note, colors.make(hue))
                            else:
                                light(msg.note, highlight)
                            if args.meter:
                                show_meter(msg.velocity)
                            changed = True
                        elif msg.type in ("note_off", "note_on"):
                            down.discard(msg.note)
                            if pedal and args.sustain:
                                sustained.add(msg.note)
                            else:
                                light(msg.note, None)
                                changed = True
                        elif msg.type == "control_change" and msg.control == 64 and args.sustain:
                            pedal = msg.value >= 64
                            if not pedal:
                                for n in sustained:
                                    light(n, None)
                                sustained.clear()
                                changed = True
                    now = time.monotonic()
                    if changed or (args.hold and now - last_send > 0.5):
                        dev.write_keys(frame.keys)
                        last_send = now
                    time.sleep(0.002)
        except KeyboardInterrupt:
            pass
        dev.write_keys(base.keys)
        if args.meter:
            dev.write_buttons(btn.load_state().values)


def cmd_palette(args) -> None:
    with open_device(args) as dev:
        n = dev.model.key_count
        keys = [(args.start + i) if args.start + i <= 0x7F else 0 for i in range(n)]
        dev.write_keys(keys)
        print("\nkey  note  byte  expected")
        for i, value in enumerate(keys):
            note = notes.name(dev.model.lowest_note + i)
            print(f"{i:>3}  {note:<4}  0x{value:02X}  {colors.describe(value)}")
        if args.hold:
            try:
                print("Holding. Ctrl+C to stop.", file=sys.stderr)
                while True:
                    time.sleep(0.5)
                    dev.write_keys(keys)
            except KeyboardInterrupt:
                pass


def cmd_buttons_probe(args) -> None:
    mapping = {}
    out = Path(args.save)
    if out.exists():
        mapping = json.loads(out.read_text(encoding="utf-8")).get("leds", {})
    value = colors.parse(args.value)
    with open_device(args) as dev:
        try:
            for i in range(args.start, args.count):
                values = [0] * args.count
                values[i] = value
                dev.write_buttons(values)
                if args.delay:
                    print(f"LED byte {i}")
                    time.sleep(args.delay)
                    continue
                answer = input(f"LED byte {i} lit - name it (Enter = skip, q = quit): ").strip()
                if answer.lower() == "q":
                    break
                if answer:
                    mapping[answer] = i
        except (KeyboardInterrupt, EOFError):
            print()
        dev.write_buttons([0] * args.count)
    if mapping:
        out.write_text(json.dumps({"count": args.count, "leds": mapping}, indent=2) + "\n", encoding="utf-8")
        print(f"Saved {len(mapping)} names to {out}")


def print_button_list() -> None:
    print("slot  name             kind")
    for name, slot in btn.LEDS.items():
        if name.startswith("strip_") and name not in ("strip_1", "strip_25"):
            continue
        kind = "colour" if slot in btn.COLOR_LEDS else "single colour (level only)"
        print(f"{slot:>4}  {name:<15}  {kind}")
    print("      (strip_2 .. strip_24 are slots 45-67)")
    print("\ngroups: " + ", ".join(f"{g} ({len(s)})" for g, s in btn.GROUPS.items() if g not in ("colour", "rgb")))
    print("values: on, medium, dim, off, or any colour (red, blue:0, red:pastel, 0x2E)")


def cmd_buttons(args) -> None:
    if args.list:
        print_button_list()
        return
    if not args.assignments:
        raise ValueError("give at least one BUTTON=VALUE (or --list)")
    extra = btn.load_extra_names(args.map)
    frame = btn.ButtonFrame() if args.reset else btn.load_state()
    if args.base:
        frame = btn.ButtonFrame(btn.parse_value(args.base))
    for item in args.assignments:
        spec, value = split_assignment(item)
        frame.set(spec, btn.parse_value(value), extra)
    with open_device(args) as dev:
        send_buttons(dev, frame, args)
        if args.hold:
            hold_buttons(dev, frame)


def cmd_strip(args) -> None:
    frame = btn.ButtonFrame() if args.reset else btn.load_state()
    if [c.lower() for c in args.colors] == ["rainbow"]:
        stops = [colors.make(h) for h in colors.RAINBOW]
    else:
        stops = [btn.parse_value(c) for c in args.colors]
    frame.strip(stops)
    if args.bar is not None:
        lit = round(max(0.0, min(100.0, args.bar)) / 100 * btn.STRIP_LEN)
        for n, slot in enumerate(btn.STRIP):
            if n >= lit:
                frame.values[slot] = colors.OFF
    with open_device(args) as dev:
        send_buttons(dev, frame, args)
        if args.hold:
            hold_buttons(dev, frame)


def cmd_raw(args) -> None:
    data = bytes(int(b, 16) for b in args.bytes)
    with open_device(args) as dev:
        dev.write_report(data)


def cmd_colors(args) -> None:
    dry = DryRunDevice(S88)
    for i, hue in enumerate(colors.HUES, start=1):
        swatch = dry.render([colors.make(hue, b) for b in range(4)])
        print(f"{hue:<13} {swatch}  bytes 0x{colors.make(hue, 0):02X}-0x{colors.make(hue, 3):02X}")


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--model", choices=sorted(MODELS), help="force a model (default: auto-detect; s88 for --dry-run)")
    common.add_argument("--path", help="exact HID path to open (see `kkleds list`)")
    common.add_argument("--dry-run", action="store_true", help="draw the keys in the terminal instead of on the keyboard")
    common.add_argument("--hold", action="store_true", help="keep re-sending until Ctrl+C (if other software overwrites the lights)")

    p = argparse.ArgumentParser(
        prog="kkleds",
        description="Customise the light guide and button LEDs of a Komplete Kontrol S88 MK2.",
        epilog="Colours: off, red, red:1 (brightness 0-3), #ff8800, or a raw byte like 0x2B. "
        "Keys: all, white, black, C (every C), C4, A0-C4, 'C,E,G'. Run `kkleds colors` for the palette.",
    )
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def add(name, func, help_):
        sp = sub.add_parser(name, parents=[common], help=help_, description=help_)
        sp.set_defaults(func=func)
        return sp

    sp = sub.add_parser("list", help="list Native Instruments HID interfaces")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("colors", help="show the colour palette in the terminal")
    sp.set_defaults(func=cmd_colors)

    add("off", cmd_off, "turn every key light off")

    sp = add("fill", cmd_fill, "set every key to one colour")
    sp.add_argument("color")

    sp = add("set", cmd_set, "colour keys or ranges: kkleds set A0-B2=blue C3-C8=green")
    sp.add_argument("assignments", nargs="+", metavar="KEYS=COLOUR")
    sp.add_argument("--base", help="start from this colour or preset file (default: off)")

    sp = add("scale", cmd_scale, "light the notes of a scale: kkleds scale D dorian")
    sp.add_argument("root", help="root note name, e.g. C, F#, Bb")
    sp.add_argument("kind", nargs="?", default="major", help=f"one of: {', '.join(notes.SCALES)}")
    sp.add_argument("--color", default="white:1", help="colour of scale notes (default white:1)")
    sp.add_argument("--root-color", default="red", help="colour of root notes (default red)")
    sp.add_argument("--other", help="colour of notes outside the scale (default: leave as base)")
    sp.add_argument("--keys", default="all", help="limit to these keys, e.g. C3-C6")
    sp.add_argument("--base", help="start from this colour or preset file (default: off)")

    sp = add("gradient", cmd_gradient, "blend colours across keys: kkleds gradient red yellow blue")
    sp.add_argument("colors", nargs="+")
    sp.add_argument("--keys", default="all")
    sp.add_argument("--base")

    sp = add("piano", cmd_piano, "different colours for white and black keys")
    sp.add_argument("--white", default="white:0")
    sp.add_argument("--black", default="blue")

    sp = add("preset", cmd_preset, "load a JSON preset (see presets/)")
    sp.add_argument("file")

    sp = add("animate", cmd_animate, "run an animated effect")
    sp.add_argument("effect", choices=sorted(EFFECTS))
    sp.add_argument("--fps", type=float, default=30)
    sp.add_argument("--speed", type=float, default=1.0)
    sp.add_argument("--color", help="hue for chase/breathe/sparkle")
    sp.add_argument("--duration", type=float, help="stop after this many seconds")
    sp.add_argument("--keep", action="store_true", help="leave the last frame on when stopping")
    sp.add_argument("--strip", action="store_true", help="mirror the effect on the touch strip")

    sp = add("follow", cmd_follow, "light keys as you play them (needs the [midi] extra)")
    sp.add_argument("--port", help="MIDI input name (substring match)")
    sp.add_argument("--list-ports", action="store_true")
    sp.add_argument("--color", default="white", help="colour of pressed keys (default white)")
    sp.add_argument("--velocity", action="store_true", help="colour pressed keys by velocity (blue=soft, red=hard)")
    sp.add_argument("--base", help="colour or preset file shown when keys are up")
    sp.add_argument("--sustain", action="store_true", help="keep keys lit while the sustain pedal is down")
    sp.add_argument("--shift", type=int, default=0, help="semitones the keyboard is transposed (octave +1 = 12)")
    sp.add_argument("--meter", action="store_true", help="show each note's velocity on the touch strip")

    sp = add("palette", cmd_palette, "calibration: key N shows colour byte START+N")
    sp.add_argument("--start", type=lambda s: int(s, 0), default=0)

    sp = add("buttons-probe", cmd_buttons_probe, "light button LEDs one at a time and name them (for other models)")
    sp.add_argument("--count", type=int, default=btn.REPORT_SIZE, help=f"bytes in the button report (default {btn.REPORT_SIZE})")
    sp.add_argument("--start", type=int, default=0)
    sp.add_argument("--value", default="0x7F", help="value to send to the lit LED (default 0x7F)")
    sp.add_argument("--delay", type=float, help="step automatically every N seconds instead of asking")
    sp.add_argument("--save", default="buttons.json", help="where to save names (default buttons.json)")

    sp = add("buttons", cmd_buttons, "set button LEDs: kkleds buttons play=on display=blue mute=red")
    sp.add_argument("assignments", nargs="*", metavar="BUTTON=VALUE")
    sp.add_argument("--list", action="store_true", help="show every button name, slot and group")
    sp.add_argument("--base", help="first set every LED to this (e.g. dim, off)")
    sp.add_argument("--reset", action="store_true", help="start from all off instead of the last state")
    sp.add_argument("--map", help="extra names from buttons-probe (JSON)")

    sp = add("strip", cmd_strip, "colour the touch strip: kkleds strip red blue | rainbow | green --bar 60")
    sp.add_argument("colors", nargs="+", help="one colour, several for a gradient, or 'rainbow'")
    sp.add_argument("--bar", type=float, metavar="PERCENT", help="light only the first PERCENT of the strip")
    sp.add_argument("--reset", action="store_true", help="turn the other buttons off too")

    sp = add("raw", cmd_raw, "send a raw HID report, hex bytes: kkleds raw 81 04 08 0c")
    sp.add_argument("bytes", nargs="+")

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    for attr in ("model", "path", "dry_run", "hold"):
        if not hasattr(args, attr):
            setattr(args, attr, None if attr in ("model", "path") else False)
    try:
        args.func(args)
    except DeviceError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
