"""Rasterize a hand-written SVG to an opaque PNG via cairosvg.

Usage: python3 svg2png.py input.svg output.png [dpi]

Fixed-DPI rasterization for the plotting plugin's diagram pipeline. SVG user
units are CSS px (96/inch), so scale = dpi/96. background_color flattens
anything left transparent onto white on top of the prelude's background rect.
"""

from __future__ import annotations

import sys

import cairosvg
from PIL import Image

DEFAULT_DPI = 200


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print("usage: python3 svg2png.py input.svg output.png [dpi]")
        return 2
    src, dst = argv[1], argv[2]
    dpi = int(argv[3]) if len(argv) == 4 else DEFAULT_DPI
    cairosvg.svg2png(
        url=src,
        write_to=dst,
        scale=dpi / 96,
        background_color="white",
    )
    with Image.open(dst) as im:
        width, height = im.size
    print(f"wrote {dst} ({width}x{height} px at {dpi} dpi)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
