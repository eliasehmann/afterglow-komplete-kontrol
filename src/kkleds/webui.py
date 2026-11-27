"""A small local web UI for the engine (standard library only).

The server listens on 127.0.0.1 only. To stop other websites from driving the
keyboard through it, the API rejects requests whose Host header isn't local
(DNS rebinding) and POSTs without the custom X-KKLeds header (cross-site
forms and simple fetches can't set it without a CORS preflight, which this
server never approves).
"""

from __future__ import annotations

import json
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .api import dispatch
from .engine import Engine

INDEX = Path(__file__).parent / "web" / "index.html"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
MAX_BODY = 1_000_000
CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:"


def make_handler(engine: Engine):
    class Handler(BaseHTTPRequestHandler):
        server_version = "kkleds"

        def log_message(self, fmt, *args):  # keep the console quiet
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            return host in LOCAL_HOSTS

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "forbidden"})
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            return self._json(*dispatch(engine, "GET", path))

        def do_POST(self):
            if not self._host_ok() or self.headers.get("X-KKLeds") != "1":
                return self._json(403, {"error": "forbidden"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self._json(413, {"error": "request too large"})
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("expected a JSON object")
            except ValueError as e:
                return self._json(400, {"error": f"bad JSON: {e}"})

            return self._json(*dispatch(engine, "POST", self.path.split("?", 1)[0], body))

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # Python's HTTPServer sets SO_REUSEADDR, which on Windows lets a second program bind
    # the same port. Ask for the port exclusively instead: a second copy of the app then
    # fails to start its server and can hand over to the first one.
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(engine: Engine, port: int = 8765) -> ThreadingHTTPServer:
    return _Server(("127.0.0.1", port), make_handler(engine))


def url_of(server: ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}/"


def serve(engine: Engine, port: int = 8765, open_browser: bool = True) -> None:
    server = make_server(engine, port)
    url = url_of(server)
    engine.start()
    print(f"kkleds UI running at {url}  (Ctrl+C to stop)")
    if engine.error:
        print(f"note: {engine.error}")
    if open_browser:
        threading.Timer(0.6, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        engine.close()
