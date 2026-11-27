"""The LED engine behind the UI.

It keeps the device open and runs the current mode on a background thread:
static looks are re-sent now and then (so NI software can't overwrite them),
animations and play-along run at their frame rate, and the touch strip can
mirror the keys or show a velocity meter. Everything is driven by the device
profile, so the engine works with any supported keyboard.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import buttons as btn
from . import colors, midi, notes, scenes
from .colors import ColorValue
from .device import DeviceError, HidDevice, MemoryDevice
from .effects import EFFECTS
from .layout import Frame
from .profile import DEFAULT_DRY_RUN, Profile, Registry

PRESETS_DIR = Path(__file__).parent / "presets"  # built-in looks (shipped with the package)
USER_PRESETS = Path.home() / ".kkleds" / "presets"
USER_SCENES = Path.home() / ".kkleds" / "scenes"
SETTINGS_FILE = Path.home() / ".kkleds" / "settings.json"
HOLD_INTERVAL = 1.0       # re-send static looks this often, so NI software can't overwrite them
RECONNECT_INTERVAL = 2.0  # look for an unplugged keyboard this often
SCENE_FPS = 30.0
STATIC_MODES = ("off", "solid", "gradient", "scale", "piano", "layers", "custom", "preset")
STRIP_MODES = ("off", "solid", "gradient", "rainbow", "bar", "mirror", "meter")
_PRESET_NAME = re.compile(r"^[A-Za-z0-9 _.-]{1,60}$")


class EngineError(ValueError):
    pass


def value(spec) -> ColorValue:
    return btn.parse_value(spec)


def hex_of(v: ColorValue) -> str:
    return "#{:02x}{:02x}{:02x}".format(*colors.preview(v))


def _preset_path(name: str) -> Optional[Path]:
    if not _PRESET_NAME.match(name or "") or ".." in name:
        raise EngineError(f"bad preset name {name!r}")
    for d in (USER_PRESETS, PRESETS_DIR):
        p = d / f"{name}.json"
        if p.is_file():
            return p
    return None


def list_presets() -> List[Dict[str, str]]:
    seen, out = set(), []
    for d, source in ((USER_PRESETS, "saved"), (PRESETS_DIR, "built-in")):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            if p.stem in seen:
                continue
            seen.add(p.stem)
            try:
                title = json.loads(p.read_text(encoding="utf-8")).get("name") or p.stem
            except (OSError, ValueError, AttributeError):
                title = p.stem
            out.append({"name": p.stem, "title": title, "source": source})
    return out


def list_custom_scenes() -> List[Dict[str, Any]]:
    out = []
    if USER_SCENES.is_dir():
        for p in sorted(USER_SCENES.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                cols = list(data["colors"])
                swatch = [hex_of(colors.parse(c)) for c in cols]
                out.append({"name": p.stem, "colors": cols, "motion": data.get("motion", "flow"), "swatch": swatch})
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return out


class Engine:
    def __init__(self, registry: Registry, profile: Optional[Profile] = None, dry_run: bool = False,
                 path: Optional[str] = None, settings_file: Optional[Path] = None):
        self.settings_file = settings_file  # None = don't remember anything (tests, CLI dry runs)
        self.registry = registry
        self.requested = profile
        self._preview_lock = threading.Lock()  # tile previews don't need (or block) the device lock
        self.dry_run = dry_run
        self.path = path
        self.lock = threading.RLock()
        self.dev = None
        self.profile: Profile = profile or registry.get(DEFAULT_DRY_RUN)
        self.error: Optional[str] = None
        self.hold = True
        self.mode: Dict[str, Any] = {"mode": "off"}
        self.last_static: Dict[str, Any] = {"mode": "off"}
        self.keys: List[ColorValue] = []
        self._static_keys: List[ColorValue] = []
        self._gen = None
        self._fps = 30.0
        self._follow: Optional[Dict[str, Any]] = None
        self._scene: Optional[Dict[str, Any]] = None
        self._buttons: Optional[btn.ButtonFrame] = None
        self.strip_mode: Dict[str, Any] = {"mode": "static"}
        self._meter = (0, 0.0)
        self._sent_keys = None
        self._sent_buttons = None
        self._last_write = 0.0
        self._base_cache: Optional[List[ColorValue]] = None  # last scene/animation frame
        self._base_t = 0.0
        # "Light up what I play": settings, and the open MIDI port while it's on
        self.play: Dict[str, Any] = {"enabled": False, "velocity": True, "color": "white", "port": None}
        self._play: Optional[Dict[str, Any]] = None
        self.play_error: Optional[str] = None
        self._dirty = False
        self._last_save = 0.0
        self._last_attempt = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._restore_settings()
        self.connect()
        if self.play["enabled"]:
            self._try_open_play()

    # ------------------------------------------------------------------ remembering settings
    def _restore_settings(self) -> None:
        if not self.settings_file:
            return
        try:
            data = json.loads(Path(self.settings_file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data.get("mode"), dict):
            self.mode = data["mode"]
        if isinstance(data.get("last_static"), dict):
            self.last_static = data["last_static"]
        self.hold = bool(data.get("hold", True))
        if isinstance(data.get("play"), dict):
            self.play.update({k: v for k, v in data["play"].items() if k in self.play})

    def _save_settings(self) -> None:
        self._dirty = False
        if not self.settings_file:
            return
        mode = self.mode if self.mode.get("mode") != "follow" else self.last_static
        data = {"mode": mode, "last_static": self.last_static, "hold": self.hold, "play": self.play}
        path = Path(self.settings_file)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass

    # ------------------------------------------------------------------ lifecycle
    def connect(self, model: Optional[str] = None) -> None:
        with self.lock:
            if model is not None:
                self.requested = None if model in ("", "auto") else self.registry.get(model)
            self._close_follow()
            if self.dev is not None:
                self.dev.close()
                self.dev = None
            try:
                if self.dry_run:
                    self.dev = MemoryDevice(self.requested or self.registry.get(DEFAULT_DRY_RUN))
                else:
                    self.dev = HidDevice.open(self.registry, self.requested, self.path)
                self.profile = self.dev.profile
                self.error = None
            except (DeviceError, ValueError) as e:
                self.dev = None
                self.error = str(e)
                self.profile = self.requested or self.profile
            p = self.profile
            self.keys = [colors.OFF] * p.key_count
            self._buttons = None
            if p.buttons:
                self._buttons = btn.ButtonFrame(p.buttons) if self.dry_run else btn.load_state(p)
            self.strip_mode = {"mode": "static"}
            self._scene = None  # rebuilt below for the (possibly new) device
            self._sent_keys = self._sent_buttons = None
            self._last_write = 0.0
            try:
                self._apply_mode(self.mode if self.mode.get("mode") != "follow" else self.last_static)
            except ValueError:
                self._apply_mode({"mode": "off"}) if p.light_guide else None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="kkleds-engine", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        with self.lock:
            if self._dirty:
                self._save_settings()
            self._close_follow()
            self._close_play()
            if self.dev is not None:
                self.dev.close()
                self.dev = None

    # ------------------------------------------------------------------ light guide modes
    def set_mode(self, spec: Dict[str, Any]) -> None:
        with self.lock:
            self._apply_mode(dict(spec))
            self._push_now()

    def _push_now(self) -> None:
        """Send a change straight away instead of waiting for the next loop tick."""
        if self._thread is None:  # not running (tests, or before start): the loop isn't sending anyway
            return
        try:
            self._tick(time.monotonic())
        except (DeviceError, OSError) as e:
            self.error = f"Lost the device: {e}"

    def _apply_mode(self, spec: Dict[str, Any]) -> None:
        m = spec.get("mode")
        self._base_cache = None
        self._dirty = True
        if m == "scene":
            self._start_scene(spec)
            return
        if self.profile.light_guide is None:
            if m == "off":
                self._scene = None
                self.mode = spec
                return
            raise EngineError(f"{self.profile.name} has no light guide")
        if m in STATIC_MODES:
            frame = self._build_static(spec)
            if m == "preset":
                self._apply_preset_buttons(spec)
            self._close_follow()
            self._gen = None
            self._scene = None
            self._static_keys = list(frame.keys)
            self.keys = list(frame.keys)
            self.mode = spec
            self.last_static = spec
        elif m == "animate":
            name = spec.get("effect", "rainbow")
            if name not in EFFECTS:
                raise EngineError(f"unknown effect {name!r}; choose from: {', '.join(EFFECTS)}")
            fps = max(1.0, min(60.0, float(spec.get("fps") or 30)))
            speed = max(0.0, min(10.0, float(spec.get("speed", 1.0))))
            color = value(spec["color"]) if spec.get("color") else None
            gen = EFFECTS[name](self.profile, fps=fps, speed=speed, color=color)
            self._close_follow()
            self._scene = None
            self._gen, self._fps, self.mode = gen, fps, spec
        elif m == "follow":
            base = self._build_static(spec.get("base") or self.last_static)
            self._close_follow()
            self._gen = None
            self._scene = None
            self._follow = self._open_follow(spec, base.keys)
            self.mode = spec
        else:
            raise EngineError(f"unknown mode {m!r}")

    def _start_scene(self, spec: Dict[str, Any]) -> None:
        scene_id = spec.get("scene", "fire")
        speed = max(0.0, min(4.0, float(spec.get("speed", 1.0))))
        brightness = scenes.brightness_factor(spec.get("brightness", "high"))
        custom = None
        if scene_id == "custom":
            specs = list(spec["colors"]) if spec.get("colors") is not None else ["red", "blue"]
            motion = spec.get("motion", "flow")
            if motion not in scenes.Custom.MOTIONS:
                raise EngineError(f"unknown motion {motion!r}; choose from: {', '.join(scenes.Custom.MOTIONS)}")
            custom = {"colors": specs, "motion": motion, "rgb": scenes.custom_colours(specs)}
        elif scene_id not in scenes.SCENES:
            raise EngineError(f"unknown scene {scene_id!r}")
        current = self._scene
        if current is not None and current["id"] == scene_id:
            current["speed"], current["brightness"] = speed, brightness  # live tweak, no restart
        else:
            current = self._scene = {"id": scene_id, "obj": scenes.make(scene_id), "speed": speed,
                                     "brightness": brightness, "t": 0.0, "last": None, "buttons": None}
        if custom:
            current["obj"].configure(custom["rgb"], custom["motion"])
        self._close_follow()
        self._gen = None
        self.mode = {"mode": "scene", "scene": scene_id, "speed": speed, "brightness": spec.get("brightness", "high")}
        if custom:
            self.mode.update(colors=custom["colors"], motion=custom["motion"], name=spec.get("name"))

    # ------------------------------------------------------------------ your own scenes
    def save_scene(self, name: str, colours: List[str], motion: str) -> str:
        if not _PRESET_NAME.match(name or "") or ".." in name:
            raise EngineError("scene names may use letters, numbers, spaces, - _ and .")
        if motion not in scenes.Custom.MOTIONS:
            raise EngineError(f"unknown motion {motion!r}")
        scenes.custom_colours(colours)  # validates
        USER_SCENES.mkdir(parents=True, exist_ok=True)
        path = USER_SCENES / f"{name}.json"
        path.write_text(json.dumps({"name": name, "colors": list(colours), "motion": motion}, indent=2) + "\n",
                        encoding="utf-8")
        return str(path)

    def delete_preset(self, name: str) -> None:
        """Delete one of your saved looks (built-in presets can't be deleted)."""
        if not _PRESET_NAME.match(name or "") or ".." in name:
            raise EngineError(f"bad look name {name!r}")
        path = USER_PRESETS / f"{name}.json"
        if not path.is_file():
            raise EngineError(f"no saved look named {name!r}")
        path.unlink()

    def delete_scene(self, name: str) -> None:
        if not _PRESET_NAME.match(name or "") or ".." in name:
            raise EngineError(f"bad scene name {name!r}")
        path = USER_SCENES / f"{name}.json"
        if not path.is_file():
            raise EngineError(f"no saved scene named {name!r}")
        path.unlink()

    def _scene_tick(self, now: float) -> List[ColorValue]:
        s = self._scene
        dt = 0.0 if s["last"] is None else min(0.2, now - s["last"])
        s["last"] = now
        s["t"] += dt * s["speed"]
        s["obj"].update(s["t"], dt * s["speed"])
        keys, s["buttons"] = scenes.render(s["obj"], self.profile, s["brightness"])
        return keys

    def _build_static(self, spec: Dict[str, Any]) -> Frame:
        f = Frame(self.profile)
        m = spec.get("mode")
        if m == "off":
            pass
        elif m == "solid":
            f.fill(value(spec.get("color", "white")))
        elif m == "gradient":
            stops = [value(c) for c in (spec.get("colors") or ["red", "blue"])]
            f.gradient(f.select(spec.get("keys") or "all"), stops)
        elif m == "scale":
            f.scale(
                f.select(spec.get("keys") or "all"),
                notes.pitch_class(spec.get("root", "C")),
                notes.scale(spec.get("scale", "major")),
                value(spec.get("color", "white:1")),
                value(spec["root_color"]) if spec.get("root_color") else None,
                value(spec["other"]) if spec.get("other") else None,
            )
        elif m == "piano":
            f.paint(f.select("white"), value(spec.get("white", "white:0")))
            f.paint(f.select("black"), value(spec.get("black", "blue")))
        elif m == "layers":
            f.apply_preset({"layers": [self._normalise_layer(l) for l in spec.get("layers") or []]})
        elif m == "custom":
            for i, v in enumerate((spec.get("keys") or [])[: len(f.keys)]):
                f.keys[i] = colors.OFF if v in (None, "") else value(v)
        elif m == "preset":
            f.apply_preset(self._read_preset(spec.get("name", "")))
        else:
            raise EngineError(f"{m!r} is not a static mode")
        return f

    @staticmethod
    def _normalise_layer(layer: Dict[str, Any]) -> Dict[str, Any]:
        """Let layers use the same words as everything else (on, dim...)."""
        out = dict(layer)
        for k in ("fill", "color", "root", "other"):
            if k in out and out[k] is not None:
                out[k] = colors.describe(value(out[k]))
        if "gradient" in out:
            out["gradient"] = [colors.describe(value(c)) for c in out["gradient"]]
        return out

    def _read_preset(self, name: str) -> Dict[str, Any]:
        path = _preset_path(name)
        if path is None:
            raise EngineError(f"no preset named {name!r}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"layers": data}

    def _apply_preset_buttons(self, spec: Dict[str, Any]) -> None:
        data = self._read_preset(spec.get("name", ""))
        if "buttons" in data and self.profile.buttons:
            self._buttons = btn.ButtonFrame(self.profile.buttons).apply(data["buttons"])
            self.strip_mode = {"mode": "static"}
            self._save_buttons()

    # ------------------------------------------------------------------ play-along
    def _open_follow(self, spec: Dict[str, Any], base: List[ColorValue]) -> Dict[str, Any]:
        mido = midi.load_mido()
        name = midi.pick_port(spec.get("port") or None)
        try:
            port = mido.open_input(name)
        except Exception as e:
            raise EngineError(f"could not open MIDI port {name!r}: {e}") from e
        return {
            "port": port, "name": name, "base": list(base), "lit": {},
            "sustained": set(), "pedal": False,
            "color": value(spec.get("color", "white")),
            "velocity": bool(spec.get("velocity")),
            "sustain": bool(spec.get("sustain")),
            "shift": int(spec.get("shift") or 0),
        }

    def _close_follow(self) -> None:
        if self._follow:
            try:
                self._follow["port"].close()
            except Exception:
                pass
            self._follow = None

    def _follow_tick(self, now: float) -> List[ColorValue]:
        f = self._follow
        lowest, count = self.profile.lowest_note, self.profile.key_count
        for msg in f["port"].iter_pending():
            if msg.type in ("note_on", "note_off"):
                i = msg.note - f["shift"] - lowest
                if not 0 <= i < count:
                    continue
                if msg.type == "note_on" and msg.velocity > 0:
                    f["sustained"].discard(i)
                    f["lit"][i] = colors.make(midi.velocity_hue(msg.velocity)) if f["velocity"] else f["color"]
                    self._meter = (msg.velocity, now)
                elif f["sustain"] and f["pedal"]:
                    f["sustained"].add(i)
                else:
                    f["lit"].pop(i, None)
            elif msg.type == "control_change" and msg.control == 64 and f["sustain"]:
                f["pedal"] = msg.value >= 64
                if not f["pedal"]:
                    for i in f["sustained"]:
                        f["lit"].pop(i, None)
                    f["sustained"].clear()
        keys = list(f["base"])
        for i, c in f["lit"].items():
            keys[i] = c
        return keys

    # ------------------------------------------------------------------ "light up what I play"
    def set_play(self, spec: Dict[str, Any]) -> None:
        """Turn the play-along overlay on/off; it lights pressed keys on top of any mode."""
        with self.lock:
            for k in ("velocity", "color", "port"):
                if k in spec:
                    self.play[k] = spec[k]
            if "color" in spec:
                value(spec["color"])  # validate
            enabled = bool(spec.get("enabled", self.play["enabled"]))
            if enabled:
                if self._play is None or "port" in spec:
                    self._close_play()
                    self._play = self._open_play()  # raises a friendly error if MIDI isn't available
            else:
                self._close_play()
            self.play["enabled"] = enabled
            self.play_error = None
            self._dirty = True
            self._push_now()

    def _open_play(self) -> Dict[str, Any]:
        mido = midi.load_mido()
        name = midi.pick_port(self.play.get("port") or None)
        try:
            port = mido.open_input(name)
        except Exception as e:
            raise EngineError(f"could not open the keyboard's MIDI input {name!r}: {e}") from e
        return {"port": port, "name": name, "lit": {}, "sustained": set(), "pedal": False}

    def _try_open_play(self) -> None:
        try:
            self._play = self._open_play()
            self.play_error = None
        except ValueError as e:
            self._play = None
            self.play_error = str(e)

    def _close_play(self) -> None:
        if self._play:
            try:
                self._play["port"].close()
            except Exception:
                pass
            self._play = None

    def _play_tick(self, keys: List[ColorValue], now: float) -> List[ColorValue]:
        p = self._play
        lowest, count = self.profile.lowest_note, self.profile.key_count
        try:
            messages = list(p["port"].iter_pending())
        except Exception as e:  # MIDI device went away
            self._close_play()
            self.play_error = f"Lost the MIDI input: {e}"
            return keys
        for msg in messages:
            if msg.type in ("note_on", "note_off"):
                i = msg.note - lowest
                if not 0 <= i < count:
                    continue
                if msg.type == "note_on" and msg.velocity > 0:
                    p["sustained"].discard(i)
                    p["lit"][i] = (colors.make(midi.velocity_hue(msg.velocity)) if self.play.get("velocity")
                                   else value(self.play.get("color") or "white"))
                    self._meter = (msg.velocity, now)
                elif p["pedal"]:
                    p["sustained"].add(i)
                else:
                    p["lit"].pop(i, None)
            elif msg.type == "control_change" and msg.control == 64:
                p["pedal"] = msg.value >= 64
                if not p["pedal"]:
                    for i in p["sustained"]:
                        p["lit"].pop(i, None)
                    p["sustained"].clear()
        if not p["lit"]:
            return keys
        keys = list(keys)
        for i, c in p["lit"].items():
            keys[i] = c
        return keys

    # ------------------------------------------------------------------ buttons
    def set_buttons(self, spec: Dict[str, Any]) -> None:
        with self.lock:
            section = self.profile.require_buttons()
            frame = self._buttons or btn.ButtonFrame(section)
            if spec.get("reset"):
                frame = btn.ButtonFrame(section)
                self.strip_mode = {"mode": "static"}
            if spec.get("base") is not None:
                frame = btn.ButtonFrame(section, value(spec["base"]))
            for name, v in (spec.get("assign") or {}).items():
                frame.set(name, value(v))
            self._buttons = frame
            if spec.get("strip"):
                self._set_strip(spec["strip"])
            self._save_buttons()
            self._push_now()

    def _set_strip(self, s: Dict[str, Any]) -> None:
        mode = s.get("mode", "off")
        if mode not in STRIP_MODES:
            raise EngineError(f"unknown strip mode {mode!r}; choose from: {', '.join(STRIP_MODES)}")
        if not self.profile.buttons.strip:
            raise EngineError(f"{self.profile.name} has no touch strip")
        f = self._buttons
        if mode in ("mirror", "meter"):
            self.strip_mode = dict(s)
            return
        if mode == "off":
            f.strip([colors.OFF])
        elif mode == "solid":
            f.strip([value(s.get("color", "white"))])
        elif mode == "gradient":
            f.strip([value(c) for c in (s.get("colors") or ["blue", "orange"])])
        elif mode == "rainbow":
            f.strip([colors.make(h) for h in colors.RAINBOW])
        elif mode == "bar":
            f.strip_bar(float(s.get("percent", 50)) / 100, value(s.get("color", "green")))
        self.strip_mode = {"mode": "static", "last": mode}

    def _save_buttons(self) -> None:
        if self._buttons is not None and not self.dry_run:
            btn.save_state(self.profile, self._buttons)

    def _compose_buttons(self, keys: List[ColorValue], now: float) -> Optional[List[ColorValue]]:
        if self._buttons is None:
            return None
        if self._scene is not None and self._scene.get("buttons") is not None:
            return list(self._scene["buttons"])
        values = list(self._buttons.values)
        strip = self.profile.buttons.strip
        mode = self.strip_mode.get("mode")
        if strip and mode == "mirror" and keys:
            n = len(keys)
            for k, slot in enumerate(strip):
                values[slot] = keys[min(n - 1, int((k + 0.5) * n / len(strip)))]
        elif strip and mode == "meter":
            velocity, t = self._meter
            level = velocity * max(0.0, 1.0 - (now - t) / 1.2)
            lit = round(level / 127 * len(strip))
            ramp = colors.gradient([colors.make(c) for c in ("green", "yellow", "red")], len(strip))
            for k, slot in enumerate(strip):
                values[slot] = ramp[k] if k < lit else colors.OFF
        return values

    # ------------------------------------------------------------------ misc actions
    def set_hold(self, hold: bool) -> None:
        with self.lock:
            self.hold = bool(hold)
            self._dirty = True

    def all_off(self) -> None:
        with self.lock:
            self._apply_mode({"mode": "off"})
            if self.profile.buttons:
                self._buttons = btn.ButtonFrame(self.profile.buttons)
                self.strip_mode = {"mode": "static"}
                self._save_buttons()
            self._push_now()

    def save_preset(self, name: str) -> str:
        if not _PRESET_NAME.match(name or "") or ".." in name:
            raise EngineError("preset names may use letters, numbers, spaces, - _ and .")
        with self.lock:
            data: Dict[str, Any] = {"name": name, "layers": [{"fill": "off"}]}
            keys, low = self.keys, self.profile.lowest_note
            i = 0
            while i < len(keys):
                j = i
                while j + 1 < len(keys) and keys[j + 1] == keys[i]:
                    j += 1
                if not colors.is_off(keys[i]):
                    sel = notes.name(low + i) if i == j else f"{notes.name(low + i)}-{notes.name(low + j)}"
                    data["layers"].append({"keys": sel, "color": colors.describe(keys[i])})
                i = j + 1
            if self._buttons is not None:
                section = self.profile.buttons
                current = self._compose_buttons(keys, time.monotonic()) or []
                assigned = {}
                for slot, v in enumerate(current):
                    if not colors.is_off(v):
                        label = section.name_of(slot)
                        assigned[label if not label.startswith("slot_") else str(slot)] = colors.describe(v)
                data["buttons"] = assigned
        USER_PRESETS.mkdir(parents=True, exist_ok=True)
        path = USER_PRESETS / f"{name}.json"
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return str(path)

    # ------------------------------------------------------------------ loop
    def _loop(self) -> None:
        while not self._stop.is_set():
            start = time.monotonic()
            with self.lock:
                if self.dev is None and not self.dry_run and start - self._last_attempt >= RECONNECT_INTERVAL:
                    self._last_attempt = start
                    self.connect()  # keyboard unplugged or switched off: keep trying
                try:
                    self._tick(start)
                except (DeviceError, OSError) as e:
                    self.error = f"Lost the device: {e}"
                    try:
                        self.dev.close()
                    except Exception:
                        pass
                    self.dev = None
                except Exception as e:  # a broken mode must not kill the engine
                    self.error = f"{type(e).__name__}: {e}"
                    self._close_follow()
                    self._gen = None
                    self._scene = None
                    self.mode = {"mode": "off"}
                    self._static_keys = [colors.OFF] * self.profile.key_count
                if self._dirty and start - self._last_save >= 1.0:
                    self._last_save = start
                    self._save_settings()
                if self._play is not None or self._follow is not None:
                    interval = 1.0 / 120  # react to playing right away
                elif self._scene is not None:
                    interval = 1.0 / SCENE_FPS
                elif self._gen is not None:
                    interval = 1.0 / self._fps
                elif self.strip_mode.get("mode") in ("mirror", "meter"):
                    interval = 1.0 / 30
                else:
                    interval = 0.1
            self._stop.wait(max(0.0, interval - (time.monotonic() - start)))

    def _base_keys(self, now: float) -> List[ColorValue]:
        """The current mode's key colours. Scenes and animations render at their own
        frame rate even when the loop runs faster for play-along."""
        if self._scene is not None or self._gen is not None:
            fps = SCENE_FPS if self._scene is not None else self._fps
            if self._base_cache is None or now - self._base_t >= 1.0 / fps:
                self._base_cache = self._scene_tick(now) if self._scene is not None else next(self._gen)
                self._base_t = now
            return self._base_cache
        if self._follow is not None:
            return self._follow_tick(now)
        return self._static_keys

    def _tick(self, now: float) -> None:
        base = self._base_keys(now)
        self.keys = self._play_tick(base, now) if self._play is not None else list(base)
        buttons = self._compose_buttons(self.keys, now)
        if self.dev is None:
            return
        due = self.hold and now - self._last_write >= HOLD_INTERVAL
        if self.profile.light_guide and (self.keys != self._sent_keys or due):
            self.dev.write_keys(self.keys)
            self._sent_keys = list(self.keys)
        if buttons is not None and (buttons != self._sent_buttons or due):
            self.dev.write_buttons(buttons)
            self._sent_buttons = buttons
        if due:
            self._last_write = now

    # ------------------------------------------------------------------ live tile previews
    PREVIEW_LEDS = 28

    def previews(self) -> Dict[str, List[str]]:
        """One animation frame for every scene tile (built-in and saved), rendered with the
        real scene code and snapped to the keyboard's palette, so tiles show what the keys do."""
        with self._preview_lock:
            now = time.monotonic()
            if not hasattr(self, "_pv"):
                self._pv: Dict[str, Any] = {}
                self._pv_start = now
            t = now - self._pv_start
            wanted = {sid: (sid, None) for sid in scenes.SCENES}
            for c in list_custom_scenes():
                wanted["custom:" + c["name"]] = ("custom", c)
            lg = self.profile.light_guide
            palette = lg is None or lg.encoding.name == "palette"
            out = {}
            for key, (sid, custom) in wanted.items():
                entry = self._pv.get(key)
                sig = json.dumps(custom, sort_keys=True) if custom else ""
                if entry is None or entry["sig"] != sig:
                    obj = scenes.make(sid, seed=7)
                    if custom:
                        try:
                            obj.configure(scenes.custom_colours(custom["colors"]), custom["motion"])
                        except ValueError:
                            continue
                    entry = self._pv[key] = {"obj": obj, "sig": sig, "last": t}
                dt = min(0.2, max(0.0, t - entry["last"]))
                entry["last"] = t
                entry["obj"].update(t, dt)
                out[key] = [hex_of(v) for v in scenes.preview(entry["obj"], self.PREVIEW_LEDS, palette)]
            for gone in set(self._pv) - set(wanted):
                del self._pv[gone]
            return out

    # ------------------------------------------------------------------ views for the UI
    def _panel_info(self) -> Optional[Dict[str, Any]]:
        """The profile's panel drawing, with every LED name resolved to its slot."""
        panel, section = self.profile.panel, self.profile.buttons
        if not panel or not section:
            return None
        labels = panel.get("labels", {})

        def item(name: str) -> Dict[str, Any]:
            if name.startswith("#"):
                return {"label": labels.get(name[1:], name[1:].replace("_", " ").upper()), "slot": None}
            slot = section.leds[name]
            return {"name": name, "slot": slot, "kind": section.kind(slot),
                    "label": labels.get(name, name.replace("_", " ").upper())}

        def resolve(block: Dict[str, Any]) -> Dict[str, Any]:
            b = dict(block)
            if b.get("type") == "buttons":
                b["rows"] = [[item(n) for n in row] for row in block.get("rows", [])]
            elif b.get("type") == "encoder":
                for d in ("up", "down", "left", "right"):
                    if d in block:
                        b[d] = item(block[d])
            return b

        return {
            "columns": [[resolve(block) for block in column] for column in panel.get("columns", [])],
            "side": [resolve(block) for block in panel.get("side", [])],
            "note": panel.get("note", ""),
        }

    def _preset_swatch(self, name: str) -> List[str]:
        """The look's real key colours, sampled down to a tile-sized row of LEDs."""
        try:
            keys = self._build_static({"mode": "preset", "name": name}).keys
        except Exception:
            return []
        n = self.PREVIEW_LEDS
        picks = [keys[int((k + 0.5) * len(keys) / n)] for k in range(n)] if keys else []
        return self._preview(picks, self.profile.light_guide.encoding if self.profile.light_guide else None)

    @staticmethod
    def _preview(values, encoding, white_only=frozenset()) -> List[str]:
        """Hex colours as the device will actually show them.

        Palette devices snap RGB to the palette, and white-only LEDs (single-colour
        buttons) show just the brightness, in white, whatever colour was sent.
        """
        out = []
        for slot, v in enumerate(values):
            if encoding is not None and encoding.name == "palette":
                v = colors.to_palette(v)
            if slot in white_only:
                hue, level = colors.split(colors.to_palette(v))
                v = colors.OFF if hue is None else colors.make("white", min(level, 2))
            out.append(hex_of(v))
        return out

    def _white_only_slots(self) -> frozenset:
        b = self.profile.buttons
        if b is None or b.color_slots is None:
            return frozenset()
        return frozenset(range(b.size)) - b.color_slots

    def state(self) -> Dict[str, Any]:
        with self.lock:
            buttons = self._compose_buttons(self.keys, time.monotonic()) or []
            mode = {k: v for k, v in self.mode.items() if k != "keys"}
            p = self.profile
            key_enc = p.light_guide.encoding if p.light_guide else None
            btn_enc = p.buttons.encoding if p.buttons else None
            return {
                "connected": self.dev is not None,
                "dry_run": self.dry_run,
                "error": self.error,
                "profile": self.profile.key,
                "mode": mode,
                "follow_port": self._follow["name"] if self._follow else None,
                "play": {"enabled": self._play is not None, "velocity": bool(self.play.get("velocity")),
                         "port": self._play["name"] if self._play else None, "error": self.play_error},
                "hold": self.hold,
                "strip": self.strip_mode,
                "keys": self._preview(self.keys, key_enc),
                "buttons": self._preview(buttons, btn_enc, self._white_only_slots()),
            }

    def info(self) -> Dict[str, Any]:
        with self.lock:
            p = self.profile
            keys = None
            if p.light_guide:
                rng = range(p.lowest_note, p.highest_note + 1)
                keys = {
                    "count": p.key_count,
                    "lowest": p.lowest_note,
                    "names": [notes.name(n) for n in rng],
                    "black": [notes.is_black(n) for n in rng],
                    "encoding": p.light_guide.encoding.name,
                }
            buttons = None
            if p.buttons:
                b = p.buttons
                strip = set(b.strip)
                buttons = {
                    "leds": [{"name": n, "slot": s, "kind": b.kind(s)}
                             for n, s in sorted(b.leds.items(), key=lambda kv: kv[1]) if s not in strip],
                    "groups": [g for g in b.groups if g not in ("everything", "colour", "rgb")],
                    "strip": list(b.strip),
                }
            palette = []
            for hue in colors.HUES:
                levels = []
                for lv, label in ((2, "2"), (1, "1"), (0, "0"), (3, "pastel")):
                    spec = f"{hue}:{label}"
                    levels.append({"spec": spec, "hex": hex_of(colors.make(hue, lv))})
                palette.append({"hue": hue, "levels": levels})
            from . import __version__

            return {
                "version": __version__,
                "profile": {"key": p.key, "name": p.name, "status": p.status, "usb": p.usb_id},
                "requested": self.requested.key if self.requested else "auto",
                "profiles": [{"key": q.key, "name": q.name, "status": q.status} for q in self.registry],
                "keys": keys,
                "buttons": buttons,
                "panel": self._panel_info(),
                "palette": palette,
                "scales": list(notes.SCALES),
                "roots": list(notes.NAMES),
                "effects": sorted(EFFECTS),
                "scenes": [{"id": s.id, "name": s.name, "description": s.description, "swatch": list(s.swatch)}
                           for s in scenes.SCENES.values()],
                "brightness": list(scenes.BRIGHTNESS),
                "motions": list(scenes.Custom.MOTIONS),
                "custom_scenes": list_custom_scenes(),
                "custom_palette": [{"spec": h, "hex": hex_of(colors.make(h, 2))} for h in colors.HUES],
                "strip_modes": list(STRIP_MODES),
                "presets": [dict(pr, swatch=self._preset_swatch(pr["name"])) for pr in list_presets()],
                "midi_ports": midi.input_names(),
            }
