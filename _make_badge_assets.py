"""Generate display-size badge assets for the danmaku UI.

honor_N.png  48x24  Douyin official honour badge (new_user_grade_level_v1_N), 1-75
club_N.png   30x30  fans-club badge: gold plate (1-10) / orange plate (11-20)
                    with a COMPLETE heart outline and the level number inside
mark_star.png / mark_admin.png are produced by _make_marks.py.
"""
from __future__ import annotations

import math
import os

from PIL import Image, ImageChops, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "assets", "badges")
OUT = os.path.join(SRC, "ui")

HONOR_W, HONOR_H = 48, 24
CLUB_SIZE = 30
SS = 10
FONT_FILE = "C:/Windows/Fonts/msyhbd.ttc"

PALETTE = {
    "gold": dict(
        plate=((255, 216, 124), (224, 154, 38)),
        border=(255, 248, 214),
        ink=(146, 78, 6),
        stroke=(132, 82, 8),
    ),
    "orange": dict(
        plate=((255, 178, 124), (206, 64, 32)),
        border=(255, 230, 205),
        ink=(140, 26, 8),
        stroke=(120, 34, 10),
    ),
}


def _heart_points(cx: float, cy: float, scale: float, n: int = 300):
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x * scale, cy - y * scale))
    return pts


def _vgrad(size: int, top, bottom) -> Image.Image:
    col = Image.new("RGB", (1, size))
    px = col.load()
    for y in range(size):
        k = y / max(1, size - 1)
        px[0, y] = tuple(round(top[i] + (bottom[i] - top[i]) * k) for i in range(3))
    return col.resize((size, size))


def club_badge(level: int) -> Image.Image:
    """Official left cap of the fans-club plate; 11+ tinted toward orange."""
    src_path = os.path.join(SRC, f"fansclub_level_v6_{level}.png")
    pill = Image.open(src_path).convert("RGBA")
    cap = pill.crop((0, 0, 48, 48))
    mask = Image.new("L", (48, 48), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, 47, 47], radius=13, fill=255)
    cap.putalpha(ImageChops.multiply(cap.getchannel("A"), mask))
    if level > 10:
        cap = _tint_orange(cap, shift=-14)
    return cap.resize((CLUB_SIZE, CLUB_SIZE), Image.LANCZOS)


def _tint_orange(im: Image.Image, shift: int = -14) -> Image.Image:
    r, g, b, a = im.split()
    h, s, v = Image.merge("RGB", (r, g, b)).convert("HSV").split()
    hp = h.load()
    sp = s.load()
    vp = v.load()
    w, ht = im.size
    for y in range(ht):
        for x in range(w):
            if sp[x, y] > 55 and 18 < hp[x, y] < 70 and vp[x, y] > 90:
                hp[x, y] = (hp[x, y] + shift) % 256
    out = Image.merge("HSV", (h, s, v)).convert("RGB").convert("RGBA")
    out.putalpha(a)
    return out


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    made = 0
    for n in range(1, 76):
        src_path = os.path.join(SRC, f"new_user_grade_level_v1_{n}.png")
        if not os.path.exists(src_path):
            continue
        im = Image.open(src_path).convert("RGBA")
        im = im.resize((HONOR_W, HONOR_H), Image.LANCZOS)
        im.save(os.path.join(OUT, f"honor_{n}.png"))
        made += 1
    for n in range(1, 21):
        club_badge(n).save(os.path.join(OUT, f"club_{n}.png"))
        made += 1
    print(f"generated {made} badge files in {OUT}")


if __name__ == "__main__":
    main()
