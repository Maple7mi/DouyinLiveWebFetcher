from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

root = Path(r"D:\DsProjects\DouyinLiveWebFetcher\ui_concepts")
items = [
    ("01-aurora", "01  Aurora Dark", "深色 · 低饱和 · 数据感"),
    ("02-paper", "02  Warm Paper", "暖白 · 纸感 · 克制"),
    ("03-primer", "03  Primer Grid", "浅色 · 工程 · 高密度"),
    ("04-discord", "04  Discord Night", "深灰 · 聊天优先"),
    ("05-twitch", "05  Stream Deck", "黑紫 · 直播控制台"),
    ("06-frost", "06  Frosted Glass", "浅色 · 玻璃 · 轻盈"),
    ("07-neon", "07  Neon Grid", "赛博 · 霓虹 · 高对比"),
    ("08-cream", "08  Cream Bloom", "奶油 · 柔和 · 亲和"),
    ("09-material", "09  Material Bloom", "Material You · 大圆角"),
    ("10-terminal", "10  Terminal Pro", "终端 · 等宽 · 复盘"),
]
W, H = 1600, 1640
canvas = Image.new("RGB", (W, H), "#0b0d12")
d = ImageDraw.Draw(canvas)
font_title = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 34)
font_sub = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 16)
font_name = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 17)
font_tag = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 12)
d.text((30, 24), "弹幕助手 · 10 版 UI 方向", font=font_title, fill="#f2f5fb")
d.text((32, 70), "同一套功能与模拟数据，只换视觉语言。选中编号后落地到 Tkinter。", font=font_sub, fill="#8d98aa")

thumb_w, thumb_h = 500, 313
label_h = 58
gap = 18
x0, y0 = 28, 112
for i, (slug, name, tag) in enumerate(items):
    col, row = i % 3, i // 3
    x = x0 + col * (thumb_w + gap)
    y = y0 + row * (thumb_h + label_h + gap)
    img = Image.open(root / "previews" / f"{slug}.png").convert("RGB").resize((thumb_w, thumb_h), Image.LANCZOS)
    canvas.paste(img, (x, y))
    d.rectangle([x, y, x + thumb_w - 1, y + thumb_h - 1], outline="#2a3040", width=1)
    d.rectangle([x, y + thumb_h, x + thumb_w, y + thumb_h + label_h], fill="#12151d")
    d.text((x + 14, y + thumb_h + 11), name, font=font_name, fill="#eef2ff")
    d.text((x + 14, y + thumb_h + 35), tag, font=font_tag, fill="#8d98aa")
    d.rectangle([x, y + thumb_h, x + 3, y + thumb_h + label_h], fill="#7c8cff")

out = root / "contact_sheet.png"
canvas.save(out)
print(out, canvas.size)
