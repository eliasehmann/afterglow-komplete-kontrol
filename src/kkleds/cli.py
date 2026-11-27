"""kkleds - LED control for Native Instruments keyboards, driven by device profiles."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import buttons as btn
from . import colors, notes
from .device import DeviceError, DryRunDevice, HidDevice, list_interfaces, path_str
from .effects import EFFECTS
from .layout import Frame
from .profile import (
    DEFAULT_DRY_RUN,
    NI_VENDOR_ID,
    USER_DIR,
    ButtonSection,
    ProfileError,
    load_file,
    load_registry,
    validate,
)


# --------------------------------------------------------------------------- helpers
def registry(args):
    if getattr(args, "_registry", None) is None:
        args._registry = load_registry(args.profile or [])
    return args._registry


def chosen_profile(args):
    return registry(args).get(args.model) if args.model else None


def connected(args) -> List[tuple]:
    """(interface info, profile or None) for every NI or profiled HID interface."""
    reg = registry(args)
    try:
        infos = list_interfaces()
    except DeviceError:
        return []
    out = []
    for d in infos:
        prof = reg.by_usb(d["vendor_id"], d["product_id"])
        if prof or d["vendor_id"] == NI_VENDOR_ID:
            out.append((d, prof))
    return out


def info_profile(args):
    """Profile for commands that don't need the device: --model, else connected, else default."""
    prof = chosen_profile(args)
    if prof:
        return prof
    for _d, p in connected(args):
        if p:
            return p
    return registry(args).get(DEFAULT_DRY_RUN)


def open_device(args):
    reg = registry(args)
    prof = chosen_profile(args)
    if args.dry_run:
        return DryRunDevice(prof or reg.get(DEFAULT_DRY_RUN))
    return HidDevice.open(reg, prof, args.path)


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


def base_frame(profile, spec: Optional[str]) -> Frame:
    """--base accepts a colour or a preset JSON file."""
    if not spec:
        return Frame(profile)
    if spec.lower().endswith(".json") or Path(spec).is_file():
        return Frame.from_preset_file(profile, spec)
    return Frame(profile, colors.parse(spec))


def send_buttons(dev, frame: "btn.ButtonFrame", args) -> None:
    dev.write_buttons(frame.values)
    if not args.dry_run:
        btn.save_state(dev.profile, frame)


def hold_buttons(dev, frame: "btn.ButtonFrame") -> None:
    print("Holding - re-sending every 0.5 s. Ctrl+C to stop.", file=sys.stderr)
    try:
        while True:
            time.sleep(0.5)
            dev.write_buttons(frame.values)
    except KeyboardInterrupt:
        pass


def strip_from_keys(keys: List, length: int) -> List:
    """Sample a light guide frame down to the touch strip's dots."""
    n = len(keys)
    return [keys[min(n - 1, int((i + 0.5) * n / length))] for i in range(length)]


def split_assignment(text: str):
    key, sep, value = text.rpartition("=")
    if not sep or not key:
        raise ValueError(f"expected KEYS=COLOUR, got {text!r}")
    return key, value


# --------------------------------------------------------------------------- device info
def cmd_list(args) -> None:
    found = connected(args)
    if not found:
        print("No Native Instruments or profiled HID devices found.")
        return
    for d, prof in found:
        if prof:
            label = f"profile {prof.key} ({prof.status})"
        else:
            label = f"no profile yet - kkleds profile-new --pid 0x{d['product_id']:04X}"
        print(
            f"{d.get('product_string') or '?'}  USB {d['vendor_id']:04X}:{d['product_id']:04X}  -> {label}\n"
            f"  interface {d.get('interface_number')}  usage_page 0x{d.get('usage_page', 0):04X}  "
            f"usage 0x{d.get('usage', 0):04X}\n"
            f"  path {path_str(d['path'])}"
        )


def cmd_devices(args) -> None:
    usb_connected = {(d["vendor_id"], d["product_id"]) for d, _p in connected(args)}
    print(f"{'model':<10} {'name':<30} {'usb':<9}  {'status':<10}  light guide / buttons")
    for p in registry(args):
        lg = p.light_guide
        b = p.buttons
        parts = []
        parts.append(f"{lg.keys} keys, {lg.encoding.describe()}" if lg else "no light guide")
        if b:
            extra = f", strip {len(b.strip)}" if b.strip else ""
            parts.append(f"{len(b.groups.get('all', []))} buttons{extra}")
        mark = "  <- connected" if (p.vendor_id, p.product_id) in usb_connected else ""
        print(f"{p.key:<10} {p.name:<30} {p.usb_id:<9}  {p.status:<10}  {' / '.join(parts)}{mark}")
    print(f"\nYour own profiles go in {USER_DIR} (see docs/adding-a-device.md).")


def cmd_profile_new(args) -> None:
    key = args.key or f"device-{args.pid:04x}"
    out = Path(args.out) if args.out else USER_DIR / f"{key}.json"
    if out.exists() and not args.force:
        raise ValueError(f"{out} already exists (add --force to overwrite)")
    variant = {"name": args.name or key, "product_id": f"0x{args.pid:04X}"}
    data = {
        "id": key,
        "name": args.name or key,
        "vendor_id": f"0x{args.vid:04X}",
        "status": "unverified",
        "source": "",
        "notes": "Starting point copied from the S MK2 layout - check every value on your device.",
        "hid": {"interface": None},
        "init": [["0xA0", "0x00", "0x00"]],
    }
    if args.keys:
        data["light_guide"] = {"report": "0x81", "encoding": "palette"}
        variant["light_guide"] = {"keys": args.keys, "lowest_note": args.lowest_note}
    data["buttons"] = {"report": "0x80", "size": args.button_slots, "encoding": "palette", "leds": {}}
    data["variants"] = {key: variant}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    print(f"Next: kkleds profile-check {out}   then   kkleds fill red --model {key}")


def cmd_profile_check(args) -> None:
    failed = 0
    for f in args.files:
        try:
            profiles = load_file(f)
        except ProfileError as e:
            print(f"{f}: ERROR {e}")
            failed += 1
            continue
        for p in profiles:
            problems = validate(p)
            status = "ok" if not problems else "PROBLEMS"
            print(f"{f} [{p.key}] {p.name} ({p.usb_id}): {status}")
            for problem in problems:
                print(f"  - {problem}")
            failed += bool(problems)
    if failed:
        raise ValueError(f"{failed} profile(s) need fixing")


# --------------------------------------------------------------------------- light guide
def cmd_off(args) -> None:
    with open_device(args) as dev:
        dev.write_keys([colors.OFF] * dev.profile.key_count)


def cmd_fill(args) -> None:
    with open_device(args) as dev:
        push(dev, Frame(dev.profile, colors.parse(args.color)), args.hold)


def cmd_set(args) -> None:
    with open_device(args) as dev:
        frame = base_frame(dev.profile, args.base)
        for item in args.assignments:
            keys, color = split_assignment(item)
            frame.paint(frame.select(keys), colors.parse(color))
        push(dev, frame, args.hold)


def cmd_scale(args) -> None:
    with open_device(args) as dev:
        frame = base_frame(dev.profile, args.base)
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
        frame = base_frame(dev.profile, args.base)
        frame.gradient(frame.select(args.keys), [colors.parse(c) for c in args.colors])
        push(dev, frame, args.hold)


def cmd_piano(args) -> None:
    with open_device(args) as dev:
        frame = Frame(dev.profile)
        frame.paint(frame.select("white"), colors.parse(args.white))
        frame.paint(frame.select("black"), colors.parse(args.black))
        push(dev, frame, args.hold)


def cmd_preset(args) -> None:
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    with open_device(args) as dev:
        has_layers = not isinstance(data, dict) or "layers" in data
        if isinstance(data, dict) and "buttons" in data:
            if dev.profile.buttons is None:
                print(f"note: {dev.profile.name} has no button LEDs; skipping the preset's buttons", file=sys.stderr)
            else:
                send_buttons(dev, btn.ButtonFrame(dev.profile.buttons).apply(data["buttons"]), args)
        if has_layers:
            push(dev, Frame(dev.profile).apply_preset(data), args.hold)


def cmd_animate(args) -> None:
    effect = EFFECTS[args.effect]
    color = colors.parse(args.color) if args.color else None
    with open_device(args) as dev:
        section = dev.profile.buttons
        if args.strip and not (section and section.strip):
            raise ValueError(f"{dev.profile.name} has no touch strip in its profile")
        saved = btn.load_state(dev.profile) if args.strip else None
        strip_values = list(saved.values) if saved else []
        frames = effect(dev.profile, fps=args.fps, speed=args.speed, color=color)
        interval = 1.0 / args.fps
        deadline = time.monotonic() + args.duration if args.duration else None
        print(f"Running {args.effect} at {args.fps} fps. Ctrl+C to stop.", file=sys.stderr)
        next_tick = time.monotonic()
        try:
            for keys in frames:
                dev.write_keys(keys)
                if saved:
                    for slot, value in zip(section.strip, strip_from_keys(keys, len(section.strip))):
                        strip_values[slot] = value
                    dev.write_buttons(strip_values)
                if deadline and time.monotonic() >= deadline:
                    break
                next_tick += interval
                time.sleep(max(0.0, next_tick - time.monotonic()))
        except KeyboardInterrupt:
            pass
        if not args.keep:
            dev.write_keys([colors.OFF] * dev.profile.key_count)
            if saved:
                dev.write_buttons(saved.values)


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
        base = base_frame(dev.profile, args.base)
        frame = base.copy()
        down = set()       # notes physically held
        sustained = set()  # notes released while the pedal was down
        pedal = False
        dev.write_keys(frame.keys)

        meter = None
        if args.meter:
            section = dev.profile.buttons
            if not (section and section.strip):
                raise ValueError(f"{dev.profile.name} has no touch strip in its profile")
            meter = btn.load_state(dev.profile)
            meter_colors = colors.gradient([colors.make(c) for c in ("green", "yellow", "red")], len(section.strip))

        def show_meter(velocity: int) -> None:
            strip = meter.section.strip
            lit = round(velocity / 127 * len(strip))
            for n, slot in enumerate(strip):
                meter.values[slot] = meter_colors[n] if n < lit else colors.OFF
            dev.write_buttons(meter.values)

        def light(note: int, color) -> None:
            i = frame.index(note - args.shift)
            if i is not None:
                frame.keys[i] = base.keys[i] if color is None else color

        print(f"Following {port_name!r}. Ctrl+C to stop.", file=sys.stderr)
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
                            if meter:
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
        if meter:
            dev.write_buttons(btn.load_state(dev.profile).values)


def cmd_palette(args) -> None:
    with open_device(args) as dev:
        n = dev.profile.key_count
        keys = [(args.start + i) if args.start + i <= 0x7F else 0 for i in range(n)]
        dev.write_keys(keys)
        print("\nkey  note  byte  expected")
        for i, value in enumerate(keys):
            note = notes.name(dev.profile.lowest_note + i)
            print(f"{i:>3}  {note:<4}  0x{value:02X}  {colors.describe(value)}")
        if args.hold:
            try:
                print("Holding. Ctrl+C to stop.", file=sys.stderr)
                while True:
                    time.sleep(0.5)
                    dev.write_keys(keys)
            except KeyboardInterrupt:
                pass


# --------------------------------------------------------------------------- buttons
def cmd_buttons_probe(args) -> None:
    value = btn.parse_value(args.value)
    with open_device(args) as dev:
        section = dev.profile.buttons
        if section is None or args.report is not None or args.count:
            section = ButtonSection.blank(
                report=args.report if args.report is not None else (section.report if section else 0x80),
                size=args.count or (section.size if section else 64),
                encoding=section.encoding if section else None,
            )
        mapping = {}
        try:
            for i in range(args.start, section.size):
                values = [colors.OFF] * section.size
                values[i] = value
                dev.write_report(section.report_bytes(values))
                if args.delay:
                    print(f"LED slot {i}")
                    time.sleep(args.delay)
                    continue
                answer = input(f"LED slot {i} lit - name it (Enter = skip, q = quit): ").strip()
                if answer.lower() == "q":
                    break
                if answer:
                    mapping[answer] = i
        except (KeyboardInterrupt, EOFError):
            print()
        dev.write_report(section.report_bytes([colors.OFF] * section.size))

    if not mapping:
        return
    out = Path(args.save)
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    buttons = data.setdefault("buttons", {})
    buttons.setdefault("report", f"0x{section.report:02X}")
    buttons.setdefault("size", section.size)
    buttons.setdefault("encoding", section.encoding.name)
    buttons.setdefault("leds", {}).update(mapping)
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(mapping)} names into {out} (buttons.leds)")


def print_button_list(profile) -> None:
    section = profile.require_buttons()
    print(f"{profile.name} - button report 0x{section.report:02X}, {section.size} slots\n")
    print("slot  name             kind")
    for name, slot in sorted(section.leds.items(), key=lambda kv: kv[1]):
        if name.startswith("strip_") and slot not in (section.strip[0], section.strip[-1]):
            continue
        print(f"{slot:>4}  {name:<15}  {section.kind(slot)}")
    if len(section.strip) > 2:
        print(f"      (strip_2 .. strip_{len(section.strip) - 1} are slots "
              f"{section.strip[1]}-{section.strip[-2]})")
    shown = {k: v for k, v in section.groups.items() if k not in ("colour", "rgb")}
    print("\ngroups: " + ", ".join(f"{g} ({len(s)})" for g, s in shown.items()))
    if section.aliases:
        print("aliases: " + ", ".join(f"{a}={t}" for a, t in section.aliases.items()))
    print("values: on, medium, dim, off, or any colour (red, blue:0, red:pastel, #ff8800)")


def cmd_buttons(args) -> None:
    if args.list:
        print_button_list(info_profile(args))
        return
    if not args.assignments:
        raise ValueError("give at least one BUTTON=VALUE (or --list)")
    extra = btn.load_extra_names(args.map)
    with open_device(args) as dev:
        section = dev.profile.require_buttons()
        frame = btn.ButtonFrame(section) if args.reset else btn.load_state(dev.profile)
        if args.base:
            frame = btn.ButtonFrame(section, btn.parse_value(args.base))
        for item in args.assignments:
            spec, value = split_assignment(item)
            frame.set(spec, btn.parse_value(value), extra)
        send_buttons(dev, frame, args)
        if args.hold:
            hold_buttons(dev, frame)


def cmd_strip(args) -> None:
    with open_device(args) as dev:
        section = dev.profile.require_buttons()
        frame = btn.ButtonFrame(section) if args.reset else btn.load_state(dev.profile)
        if [c.lower() for c in args.colors] == ["rainbow"]:
            stops = [colors.make(h) for h in colors.RAINBOW]
        else:
            stops = [btn.parse_value(c) for c in args.colors]
        frame.strip(stops)
        if args.bar is not None:
            lit = round(max(0.0, min(100.0, args.bar)) / 100 * len(section.strip))
            for n, slot in enumerate(section.strip):
                if n >= lit:
                    frame.values[slot] = colors.OFF
        send_buttons(dev, frame, args)
        if args.hold:
            hold_buttons(dev, frame)


def cmd_raw(args) -> None:
    data = bytes(int(b, 16) for b in args.bytes)
    with open_device(args) as dev:
        dev.write_report(data)


def cmd_colors(args) -> None:
    dry = DryRunDevice(info_profile(args))
    for hue in colors.HUES:
        swatch = dry.blocks([colors.make(hue, b) for b in range(4)])
        print(f"{hue:<13} {swatch}  bytes 0x{colors.make(hue, 0):02X}-0x{colors.make(hue, 3):02X}"
              "  (levels dim, medium, bright, pastel)")


# --------------------------------------------------------------------------- parser
def _int0(text: str) -> int:
    return int(text, 0)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--model", help="device profile to use, e.g. s88-mk2 (default: auto-detect; see `kkleds devices`)")
    common.add_argument("--profile", action="append", metavar="FILE", help="load an extra profile JSON (repeatable)")
    common.add_argument("--path", help="exact HID path to open (see `kkleds list`)")
    common.add_argument("--dry-run", action="store_true", help="draw the LEDs in the terminal instead of on the keyboard")
    common.add_argument("--hold", action="store_true", help="keep re-sending until Ctrl+C (if other software overwrites the lights)")

    p = argparse.ArgumentParser(
        prog="kkleds",
        description="Customise the LEDs of Native Instruments keyboards. Device support comes from profiles.",
        epilog="Colours: off, red, red:0-2, red:pastel, #ff8800, or a raw palette byte like 0x2B. "
        "Keys: all, white, black, C (every C), C4, A0-C4, 'C,E,G'. Run `kkleds colors` for the palette.",
    )
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def add(name, func, help_):
        sp = sub.add_parser(name, parents=[common], help=help_, description=help_)
        sp.set_defaults(func=func)
        return sp

    # device info / profiles
    add("list", cmd_list, "list connected NI HID interfaces and their profiles")
    add("devices", cmd_devices, "list every known device profile")
    add("colors", cmd_colors, "show the colour palette in the terminal")

    sp = add("profile-new", cmd_profile_new, "write a starter profile for an unsupported device")
    sp.add_argument("--pid", type=_int0, required=True, help="USB product ID, e.g. 0x1720")
    sp.add_argument("--vid", type=_int0, default=NI_VENDOR_ID, help="USB vendor ID (default 0x17CC)")
    sp.add_argument("--key", help="short model id, e.g. a49-mk1 (default: device-<pid>)")
    sp.add_argument("--name", help="display name")
    sp.add_argument("--keys", type=int, help="number of keys with a light guide (omit if none)")
    sp.add_argument("--lowest-note", type=int, default=36, help="MIDI note of the leftmost key (default 36)")
    sp.add_argument("--button-slots", type=int, default=64, help="button report size to start probing with")
    sp.add_argument("--out", help=f"where to write it (default {USER_DIR}\\<key>.json)")
    sp.add_argument("--force", action="store_true")

    sp = add("profile-check", cmd_profile_check, "validate profile JSON files")
    sp.add_argument("files", nargs="+")

    # light guide
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

    sp = add("palette", cmd_palette, "calibration: key N shows palette byte START+N")
    sp.add_argument("--start", type=_int0, default=0)

    # buttons
    sp = add("buttons", cmd_buttons, "set button LEDs: kkleds buttons play=on display=blue mute=red")
    sp.add_argument("assignments", nargs="*", metavar="BUTTON=VALUE")
    sp.add_argument("--list", action="store_true", help="show every button name, slot and group")
    sp.add_argument("--base", help="first set every LED to this (e.g. dim, off)")
    sp.add_argument("--reset", action="store_true", help="start from all off instead of the last state")
    sp.add_argument("--map", help="extra names, e.g. from buttons-probe (JSON)")

    sp = add("strip", cmd_strip, "colour the touch strip: kkleds strip red blue | rainbow | green --bar 60")
    sp.add_argument("colors", nargs="+", help="one colour, several for a gradient, or 'rainbow'")
    sp.add_argument("--bar", type=float, metavar="PERCENT", help="light only the first PERCENT of the strip")
    sp.add_argument("--reset", action="store_true", help="turn the other buttons off too")

    sp = add("buttons-probe", cmd_buttons_probe, "light LED slots one at a time and name them (to map a new device)")
    sp.add_argument("--report", type=_int0, help="button report ID (default: from the profile, else 0x80)")
    sp.add_argument("--count", type=int, help="number of slots to probe (default: from the profile, else 64)")
    sp.add_argument("--start", type=int, default=0)
    sp.add_argument("--value", default="white", help="value for the lit slot (default white)")
    sp.add_argument("--delay", type=float, help="step automatically every N seconds instead of asking")
    sp.add_argument("--save", default="buttons-probe.json",
                    help="JSON to save names into; pass your profile file to update its buttons.leds")

    sp = add("raw", cmd_raw, "send a raw HID report, hex bytes: kkleds raw 81 04 08 0c")
    sp.add_argument("bytes", nargs="+")

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except DeviceError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as e:  # includes ProfileError
        print(f"\nerror: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
