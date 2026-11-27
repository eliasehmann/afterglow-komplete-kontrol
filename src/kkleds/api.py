"""The UI's API, independent of HTTP.

The local server (webui.py) and the web version (browser.py) both route every
/api/... request through dispatch(), so the page behaves the same in both.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from .device import DeviceError
from .engine import Engine


def dispatch(engine: Engine, method: str, path: str, body: Optional[dict] = None) -> Tuple[int, Any]:
    if method == "GET":
        if path == "/api/info":
            return 200, engine.info()
        if path == "/api/state":
            return 200, engine.state()
        if path == "/api/previews":
            return 200, engine.previews()
        return 404, {"error": "not found"}
    body = body or {}
    try:
        extra = {}
        if path == "/api/mode":
            engine.set_mode(body)
        elif path == "/api/buttons":
            engine.set_buttons(body)
        elif path == "/api/play":
            engine.set_play(body)
        elif path == "/api/hold":
            engine.set_hold(body.get("hold", True))
        elif path == "/api/connect":
            engine.connect(body.get("model"))
        elif path == "/api/off":
            engine.all_off()
        elif path == "/api/presets/save":
            extra["path"] = engine.save_preset(str(body.get("name", "")).strip())
        elif path == "/api/presets/delete":
            engine.delete_preset(str(body.get("name", "")).strip())
        elif path == "/api/scenes/save":
            extra["path"] = engine.save_scene(str(body.get("name", "")).strip(),
                                              list(body.get("colors") or []), str(body.get("motion", "flow")))
        elif path == "/api/scenes/delete":
            engine.delete_scene(str(body.get("name", "")).strip())
        else:
            return 404, {"error": "not found"}
    except (ValueError, KeyError, TypeError, DeviceError) as e:
        return 400, {"error": str(e)}
    return 200, {"ok": True, "state": engine.state(), **extra}
