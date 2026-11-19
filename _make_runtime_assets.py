from PIL import Image
from pathlib import Path

SRC = Path(r"D:\DsProjects\DouyinLiveWebFetcher\assets\badges\ui")
OUT = Path(r"D:\DsProjects\DouyinLiveWebFetcher\assets\ui_badges")
OUT.mkdir(parents=True, exist_ok=True)

# every inline icon is exactly as tall as the 18pt chat text (24 px @96dpi)
HONOR_SIZE = (48, 24)
CLUB_SIZE = (24, 24)
MARK_SIZE = (24, 24)


def fit(src_name: str, dst_name: str, size):
    im = Image.open(SRC / src_name).convert("RGBA")
    im = im.resize(size, Image.LANCZOS)
    im.save(OUT / dst_name)
    return 1


made = 0
for n in range(1, 76):
    p = SRC / f"honor_{n}.png"
    if p.exists():
        made += fit(p.name, f"honor_{n}.png", HONOR_SIZE)
for n in range(1, 21):
    p = SRC / f"club_{n}.png"
    if p.exists():
        made += fit(p.name, f"club_{n}.png", CLUB_SIZE)
for name in ("mark_star.png", "mark_admin.png"):
    if (SRC / name).exists():
        made += fit(name, name, MARK_SIZE)
print("runtime assets:", made, "->", OUT)
