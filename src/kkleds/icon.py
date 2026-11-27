"""The app icon, drawn in code: a row of coloured LEDs above a few piano keys."""

from __future__ import annotations

LED_COLOURS = [(255, 59, 59), (255, 138, 0), (255, 214, 0), (62, 207, 98), (0, 190, 255), (88, 96, 255), (190, 70, 255)]


def make_icon(size: int = 64):
    from PIL import Image, ImageDraw

    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=max(2, s // 5), fill=(21, 23, 28, 255))

    # light guide: one LED per white key
    left, right = s * 0.13, s * 0.87
    n = len(LED_COLOURS)
    w = (right - left) / n
    for i, colour in enumerate(LED_COLOURS):
        cx = left + w * (i + 0.5)
        r = max(1.0, w * 0.32)
        d.ellipse([cx - r, s * 0.24 - r, cx + r, s * 0.24 + r], fill=colour + (255,))

    # keys
    top, bottom = s * 0.36, s * 0.86
    d.rectangle([left, top, right, bottom], fill=(236, 236, 232, 255))
    for i in range(1, n):
        x = left + w * i
        d.line([x, top, x, bottom], fill=(150, 150, 146, 255), width=max(1, s // 64))
    for i in (1, 2, 4, 5, 6):  # black keys between white ones, like a real octave
        x = left + w * i
        d.rectangle([x - w * 0.3, top, x + w * 0.3, top + (bottom - top) * 0.58], fill=(12, 12, 14, 255))
    return img


def save_ico(path) -> None:
    make_icon(256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
