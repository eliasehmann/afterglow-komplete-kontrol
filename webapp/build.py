"""Build the Kontrol LEDs website into site/ (static files, host anywhere with HTTPS).

    python webapp/build.py [OUTPUT_DIR]

The page is the app's own src/kkleds/web/index.html plus webapp/kkleds-web.js,
and kkleds.zip holds the Python package that runs in the browser via Pyodide.
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "kkleds"
# Modules a browser can't use (tray, autostart, local web server, command line).
DESKTOP_ONLY = {"app.py", "autostart.py", "icon.py", "webui.py", "cli.py", "__main__.py"}

HEAD = """<meta name="description" content="Make your Komplete Kontrol keyboard glow: ambient scenes, your own colours on every key, a scale guide and play-along lights. Runs in Chrome or Edge, nothing to install.">
<link rel="icon" href="icon.png">
<script src="kkleds-web.js"></script>"""


def build(out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    html = (SRC / "web" / "index.html").read_text(encoding="utf-8")
    if "</title>" not in html:
        raise SystemExit("index.html has no </title> to inject after")
    (out / "index.html").write_text(html.replace("</title>", "</title>\n" + HEAD, 1), encoding="utf-8")
    shutil.copy(ROOT / "webapp" / "kkleds-web.js", out / "kkleds-web.js")

    with zipfile.ZipFile(out / "kkleds.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(SRC.rglob("*")):
            if p.is_dir() or "__pycache__" in p.parts or p.suffix not in (".py", ".json"):
                continue
            if p.parent == SRC and p.name in DESKTOP_ONLY:
                continue
            z.write(p, p.relative_to(SRC.parent).as_posix())

    sys.path.insert(0, str(SRC.parent))
    from kkleds.icon import make_icon  # needs Pillow (the [app] extra)

    make_icon(128).save(out / "icon.png")
    (out / ".nojekyll").touch()  # GitHub Pages: serve files as they are
    return out


if __name__ == "__main__":
    target = build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "site")
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    print(f"Built {target} ({size // 1024} KB, plus Pyodide from the CDN)")
