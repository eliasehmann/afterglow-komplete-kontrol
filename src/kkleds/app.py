"""The desktop app.

Double-click it and the lights come on with your last look, the UI opens in
the browser, and an icon sits in the system tray (Open, Turn lights off,
Start with Windows, Quit). Starting it again while it's running just opens the
UI. Windows starts it with --background at login (no browser window).
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.request
import webbrowser
from typing import List, Optional

from . import __version__

APP_NAME = "Kontrol LEDs"
DEFAULT_PORT = 8765


def _already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1.5) as r:
            return "connected" in json.loads(r.read())
    except (OSError, ValueError):
        return False


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="KontrolLEDs", description=f"{APP_NAME} {__version__}")
    p.add_argument("--background", action="store_true", help="start without opening the browser (used at login)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--dry-run", action="store_true", help="preview only, don't touch the keyboard")
    args = p.parse_args(argv)

    if _already_running(args.port):  # second launch: just show the running app
        if not args.background:
            webbrowser.open(f"http://127.0.0.1:{args.port}/")
        return 0

    from .engine import SETTINGS_FILE, Engine
    from .profile import load_registry
    from .webui import make_server, url_of

    engine = Engine(load_registry(), dry_run=args.dry_run, settings_file=SETTINGS_FILE)
    try:
        server = make_server(engine, args.port)
    except OSError:  # some other program uses the port: take any free one
        server = make_server(engine, 0)
    url = url_of(server)
    engine.start()
    threading.Thread(target=server.serve_forever, name="kkleds-web", daemon=True).start()
    if not args.background:
        webbrowser.open(url)

    try:
        _run_tray(engine, url)
    finally:
        server.shutdown()
        server.server_close()
        engine.close()
    return 0


def _run_tray(engine, url: str) -> None:
    try:
        import pystray

        from .icon import make_icon
    except ImportError:  # no tray support installed: run until Ctrl+C
        print(f"{APP_NAME} is running at {url}  (Ctrl+C to quit)")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return

    from . import autostart

    def open_ui(icon, item):
        webbrowser.open(url)

    def lights_off(icon, item):
        engine.all_off()

    def toggle_autostart(icon, item):
        autostart.set_enabled(not autostart.is_enabled())

    def quit_app(icon, item):
        engine.all_off()  # leave the keyboard dark rather than frozen on the last frame
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(f"Open {APP_NAME}", open_ui, default=True),
        pystray.MenuItem("Turn lights off", lights_off),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start with Windows", toggle_autostart,
                         checked=lambda item: autostart.is_enabled(), visible=autostart.supported()),
        pystray.MenuItem("Quit", quit_app),
    )
    pystray.Icon("kontrol-leds", make_icon(64), APP_NAME, menu).run()


if __name__ == "__main__":
    sys.exit(main())
