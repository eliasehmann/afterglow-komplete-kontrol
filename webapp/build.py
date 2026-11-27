"""Build the Afterglow website into site/ (static files, host anywhere with HTTPS).

    python webapp/build.py [OUTPUT_DIR] [--url https://where.it.will.live/]

The page is the app's own src/kkleds/web/index.html plus webapp/kkleds-web.js,
and kkleds.zip holds the Python package that runs in the browser via Pyodide.
--url sets the site's address for search engines and link previews
(canonical link, Open Graph, sitemap); without it those absolute links are left out.
"""

from __future__ import annotations

import argparse
import html as htmllib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "kkleds"
# Modules a browser can't use (tray, autostart, local web server, command line).
DESKTOP_ONLY = {"app.py", "autostart.py", "icon.py", "webui.py", "cli.py", "__main__.py"}

TITLE = "Afterglow: Komplete Kontrol light guide colours, LED scenes & play-along lights"
DESCRIPTION = ("Set your own colours on the light guide of your Native Instruments Komplete Kontrol "
               "S88, S61 or S49 MK2. Ambient LED scenes (fire, ocean, aurora), paint every key, a scale "
               "guide and keys that light up as you play. Free, runs in Chrome or Edge, nothing to install.")
KEYWORDS = ("Komplete Kontrol, light guide, light guide colours, S88 MK2, S61 MK2, S49 MK2, Native Instruments, "
            "keyboard LED, RGB, LED scenes, scale guide, WebHID, MIDI")


def head(url: str | None) -> str:
    e = htmllib.escape
    tags = [
        f'<meta name="description" content="{e(DESCRIPTION)}">',
        f'<meta name="keywords" content="{e(KEYWORDS)}">',
        '<meta name="theme-color" content="#0c0d10">',
        '<link rel="icon" href="icon.png">',
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="Afterglow">',
        f'<meta property="og:title" content="{e(TITLE)}">',
        f'<meta property="og:description" content="{e(DESCRIPTION)}">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{e(TITLE)}">',
        f'<meta name="twitter:description" content="{e(DESCRIPTION)}">',
    ]
    app = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "Afterglow",
        "description": DESCRIPTION,
        "applicationCategory": "MultimediaApplication",
        "operatingSystem": "Windows, Chrome, Edge",
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "EUR"},
        "keywords": KEYWORDS,
    }
    if url:
        url = url.rstrip("/") + "/"
        tags += [
            f'<link rel="canonical" href="{e(url)}">',
            f'<meta property="og:url" content="{e(url)}">',
            f'<meta property="og:image" content="{e(url)}screenshot.png">',
            f'<meta name="twitter:image" content="{e(url)}screenshot.png">',
        ]
        app.update(url=url, image=url + "screenshot.png")
    tags.append(f'<script type="application/ld+json">{json.dumps(app)}</script>')
    tags.append('<script src="kkleds-web.js"></script>')
    return "\n".join(tags)


def build(out: Path, url: str | None = None) -> Path:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    page = (SRC / "web" / "index.html").read_text(encoding="utf-8")
    if "<title>Afterglow</title>" not in page:
        raise SystemExit("index.html has no <title>Afterglow</title> to replace")
    page = page.replace("<title>Afterglow</title>", f"<title>{htmllib.escape(TITLE)}</title>\n{head(url)}", 1)
    (out / "index.html").write_text(page, encoding="utf-8")
    shutil.copy(ROOT / "webapp" / "kkleds-web.js", out / "kkleds-web.js")
    shutil.copy(ROOT / "docs" / "screenshot.png", out / "screenshot.png")  # link previews

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
    if url:
        base = url.rstrip("/") + "/"
        (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}sitemap.xml\n", encoding="utf-8")
        (out / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{htmllib.escape(base)}</loc></url></urlset>\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the Afterglow website")
    ap.add_argument("out", nargs="?", default=str(ROOT / "site"))
    ap.add_argument("--url", help="the site's public address, e.g. https://example.github.io/afterglow/")
    args = ap.parse_args()
    target = build(Path(args.out), args.url)
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    print(f"Built {target} ({size // 1024} KB, plus Pyodide from the CDN)")
