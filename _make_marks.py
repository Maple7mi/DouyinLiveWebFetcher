"""Generate the round 星守护 / 房管 marks for the danmaku rows.

Blue circle with 星 = 星守护, red circle with 管 = 房管.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "assets", "badges", "ui")

SIZE = 24
SS = 8
FONT = "C:/Windows/Fonts/msyhbd.ttc"


def radial(size: int, inner, outer) -> Image.Image:
    img = Image.new("RGB", (size, size), outer)
    px = img.load()
    cx = cy = (size - 1) / 2
    maxd = ((cx) ** 2 + (cy) ** 2) ** 0.5
    for y in range(size):
        for x in range(size):
            d = (((x - cx) ** 2 + (y - cy) ** 2) ** 0.5) / maxd
            px[x, y] = tuple(
                round(inner[i] + (outer[i] - inner[i]) * min(1.0, d * 1.05)) for i in range(3)
            )
    return img


def mark(char: str, inner, outer, ring) -> Image.Image:
    s = SIZE * SS
    base = radial(s, inner, outer).convert("RGBA")
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, s - 1, s - 1], fill=255)
    base.putalpha(mask)

    d = ImageDraw.Draw(base)
    d.ellipse(
        [int(s * 0.04), int(s * 0.04), int(s * 0.96), int(s * 0.96)],
        outline=ring,
        width=max(1, int(s * 0.05)),
    )
    font = ImageFont.truetype(FONT, int(s * 0.56))
    bbox = d.textbbox((0, 0), char, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(
        ((s - tw) / 2 - bbox[0], (s - th) / 2 - bbox[1] - s * 0.02),
        char,
        font=font,
        fill=(255, 255, 255),
    )
    return base.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    mark("星", (86, 160, 255), (32, 82, 190), (150, 205, 255)).save(
        os.path.join(OUT, "mark_star.png")
    )
    mark("管", (255, 96, 128), (200, 30, 70), (255, 170, 190)).save(
        os.path.join(OUT, "mark_admin.png")
    )
    print("marks written")


if __name__ == "__main__":
    main()
