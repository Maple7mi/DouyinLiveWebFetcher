#!/usr/bin/env python
# coding: utf-8
"""弹幕助手 · Aurora Dark

单一弹幕流：聊天 + 粉丝团 Lv.15+ 入场混排；
底部可滚动礼物栏，默认显示最近 8 条 >=50 钻礼物，格式为：
    消费Lv.26 粉丝Lv.16 小番茄 送出了【跑车】x1，价值1,200钻
界面不显示时间戳，但 CSV / 导出网站仍保留完整时间。
"""

from __future__ import annotations

import ctypes
import os
from PIL import Image
import queue
import re
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from collect import parse_live_id
from browser_login import (
    BrowserLoginCancelled,
    BrowserLoginError,
    clear_login_profile,
    login_and_get_cookie,
)
from danmaku_gui import DanmakuClient, app_dir, fmt_int

# ---------------------------------------------------------------------------
# Aurora Dark
# ---------------------------------------------------------------------------
BG = "#070a10"
CARD = "#11151f"
CARD_ALT = "#0c1018"
FIELD = "#0a0e15"
BORDER = "#202635"
FG = "#e8edf5"
MUTED = "#8b96a8"
ACCENT = "#7c5cff"
ACCENT_DIM = "#9d86ff"
CYAN = "#00e5ff"
MAGENTA = "#ff2d95"
GOLD = "#f5c451"
GREEN = "#2dd4a7"
RED = "#ff5d73"
BLUE = "#5aa9ff"
FONT = "Microsoft YaHei UI"


_BADGE_ORDER = {"房管": 0, "会员": 1, "星守护": 2}


def resource_dir() -> Path:
    """Bundled read-only assets (PyInstaller extracts them to _MEIPASS)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    return Path(__file__).resolve().parent



# ---------------------------------------------------------------------------
# 抖音小表情（[名字] token → 本地 PNG）
# ---------------------------------------------------------------------------
_EMOJI_RE = re.compile(r"\[[^\[\]\s]{1,12}\]")
_CONTENT_FONT_PX = 24
_LINE_H = 36
_EMOJI_PX = 26
_DY_EMOJI: dict[str, str] | None = None
_EMOJI_PIL: dict[str, object] = {}
_CONTENT_FONT = None


_SYMBOL_FONT = None
_FONTS_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


def _content_font():
    global _CONTENT_FONT
    if _CONTENT_FONT is None:
        try:
            from PIL import ImageFont

            _CONTENT_FONT = ImageFont.truetype(str(_FONTS_DIR / "msyh.ttc"), _CONTENT_FONT_PX)
        except Exception:
            _CONTENT_FONT = False
    return _CONTENT_FONT or None


def _symbol_font():
    """雅黑缺 ➕ 之类的符号，回退到 Segoe UI Symbol。"""
    global _SYMBOL_FONT
    if _SYMBOL_FONT is None:
        try:
            from PIL import ImageFont

            for name in ("seguisym.ttf", "seguisym.ttc", "seguiemj.ttf"):
                try:
                    _SYMBOL_FONT = ImageFont.truetype(str(_FONTS_DIR / name), _CONTENT_FONT_PX)
                    break
                except Exception:
                    continue
        except Exception:
            pass
    return _SYMBOL_FONT or None


_SYMBOL_SUBS = {'\u2795': '\uff0b', '\u2714': '\u221a'}


def _needs_symbol(ch: str) -> bool:
    o = ord(ch)
    if 0x2190 <= o <= 0x2BFF:
        return True
    if 0x1F000 <= o <= 0x1FAFF:
        return True
    return o in (0xFE0F, 0x200D, 0x20E3)


def _font_for(ch: str):
    if _needs_symbol(ch):
        sym = _symbol_font()
        if sym is not None:
            return sym
    return _content_font()


def dy_emoji_map() -> dict[str, str]:
    global _DY_EMOJI
    if _DY_EMOJI is None:
        table: dict[str, str] = {}
        try:
            raw = (resource_dir() / "assets" / "dy_emoji" / "map.tsv").read_text(
                encoding="utf-8"
            )
            for line in raw.splitlines():
                if "\t" in line:
                    name, slug = line.split("\t", 1)
                    table[name.strip()] = slug.strip()
        except Exception:
            table = {}
        _DY_EMOJI = table
    return _DY_EMOJI


def _emoji_pil(slug: str):
    from PIL import Image

    cached = _EMOJI_PIL.get(slug)
    if cached is not None:
        return cached
    path = resource_dir() / "assets" / "dy_emoji" / f"{slug}.png"
    try:
        im = Image.open(path).convert("RGBA")
    except Exception:
        _EMOJI_PIL[slug] = False
        return None
    im = im.resize((_EMOJI_PX, _EMOJI_PX), Image.LANCZOS)
    _EMOJI_PIL[slug] = im
    return im


def has_dy_emoji(text: str) -> bool:
    table = dy_emoji_map()
    if not table:
        return False
    for m in _EMOJI_RE.finditer(text or ""):
        if m.group(0) in table:
            return True
    return False


def render_rich_content(text: str, color: str, max_w: int):
    """把含 [表情] 的弹幕渲染成一张图（文字 + 表情内联），不需要时返回 None。"""
    font = _content_font()
    table = dy_emoji_map()
    if font is None or not table:
        return None
    from PIL import Image, ImageDraw

    items: list[tuple[str, str]] = []
    pos = 0
    for m in _EMOJI_RE.finditer(text or ""):
        tok = m.group(0)
        slug = table.get(tok)
        if slug is None:
            continue
        if m.start() > pos:
            items.append(("t", text[pos : m.start()]))
        items.append(("e", slug))
        pos = m.end()
    if pos < len(text or ""):
        items.append(("t", text[pos:]))
    if not any(k == "e" for k, _ in items):
        return None

    width = max(120, int(max_w))
    lines: list[list[tuple[str, str]]] = [[]]
    used = 0.0
    for kind, val in items:
        if kind == "e":
            step = _EMOJI_PX + 2
            if used + step > width and lines[-1]:
                lines.append([])
                used = 0.0
            lines[-1].append(("e", val))
            used += step
            continue
        for ch in val:
            ch = _SYMBOL_SUBS.get(ch, ch)
            cw = _font_for(ch).getlength(ch)
            if used + cw > width and lines[-1]:
                lines.append([])
                used = 0.0
            if lines[-1] and lines[-1][-1][0] == "t":
                lines[-1][-1] = ("t", lines[-1][-1][1] + ch)
            else:
                lines[-1].append(("t", ch))
            used += cw

    ascent, descent = font.getmetrics()
    height = max(1, len(lines)) * _LINE_H
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    y = 0
    for line in lines:
        x = 0.0
        baseline = y + 3 + ascent
        for kind, val in line:
            if kind == "e":
                pic = _emoji_pil(val)
                if pic is None:
                    draw.text((x, baseline), f"[{val}]", font=font, fill=color, anchor="ls")
                    x += font.getlength(f"[{val}]")
                    continue
                top = baseline + 2 - pic.height
                canvas.paste(pic, (int(x), int(top)), pic)
                x += pic.width + 2
            else:
                for ch in val:
                    ch = _SYMBOL_SUBS.get(ch, ch)
                    fch = _font_for(ch)
                    draw.text((x, baseline), ch, font=fch, fill=color, anchor="ls")
                    x += fch.getlength(ch)
        y += _LINE_H
    return canvas



def _badge_identity(label: str, url: str = "", uri: str = "") -> str | None:
    label = str(label or "").strip()
    blob = f"{label} {url or ''} {uri or ''}".lower()
    if "webcast_admin_badge" in blob:
        return "房管"
    if "star_guard" in blob:
        return "星守护"
    if "member" in blob or "vip" in blob or "会员" in blob:
        return "会员"
    # 粉丝团等级、消费/荣誉等级已有独立列，不再重复塞进「标识」。
    if any(x in blob for x in ("fansclub", "粉丝团", "new_user_grade", "honor", "荣誉等级")):
        return None
    # 星守护等徽章的 content.name 为空时，label 会是 webcast/xxx.png；过滤掉。
    if not label or "http" in label.lower() or "webcast/" in label or label.lower().endswith(
        (".png", ".webp", ".gif", ".jpg", ".jpeg")
    ):
        return None
    return label[:6]


def _badge_text(event: dict) -> str:
    found: list[str] = []
    for item in event.get("badge_items") or []:
        if not isinstance(item, dict):
            continue
        who = _badge_identity(
            str(item.get("label") or ""),
            str(item.get("url") or ""),
            str(item.get("uri") or ""),
        )
        if who and who not in found:
            found.append(who)
    if not found:
        # 兼容只有 badge_names 的旧事件 / 历史数据
        for name in str(event.get("badge_names") or "").split("|"):
            who = _badge_identity(name)
            if who and who not in found:
                found.append(who)
    if not found:
        return "—"
    found.sort(key=lambda x: _BADGE_ORDER.get(x, 9))
    return "/".join(found)
APP_NAME = "弹幕助手"
WINDOW_TITLE = f"{APP_NAME} · Aurora Dark"
GIFT_LIMIT = 200
GIFT_MIN_DEFAULT = 50
ADMIN_BG = "#1c2406"
ADMIN_BAR = "#c6f432"
ADMIN_NAME = "#dcff8a"
ADMIN_BODY = "#f2ffd0"
CHAT_LIMIT = 500
CONTRIB_VISIBLE = 5
CONTRIB_KEEP = 50  # 榜单最多保留的贡献人数
_SINGLE_MUTEX = None


def _is_badge_test() -> bool:
    return getattr(sys, "frozen", False) and "badge-test" in Path(sys.executable).name.lower()


def _focus_existing_window() -> bool:
    """Windows 下若已有同名窗口，直接聚焦它，避免重复开窗。"""
    if sys.platform != "win32" or _is_badge_test():
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            return True
    except Exception:
        pass
    return False


def _acquire_single_instance() -> bool:
    global _SINGLE_MUTEX
    if sys.platform != "win32":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        mutex_name = "Local\\DanmakuAssistantAurora"
        if _is_badge_test():
            mutex_name += "BadgeTest"
        _SINGLE_MUTEX = kernel32.CreateMutexW(None, False, mutex_name)
        if not _SINGLE_MUTEX:
            return True
        return kernel32.GetLastError() != 183
    except Exception:
        return True


class AuroraApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry("1600x1000")
        self.root.minsize(1280, 800)
        self.root.configure(bg=BG)

        self.events: queue.Queue = queue.Queue()
        self.client: DanmakuClient | None = None
        self.thread: threading.Thread | None = None

        self.chat_count = 0
        self.gift_count = 0
        self.total_events = 0
        self.gift_rows: dict[str, dict] = {}
        self.gift_order: list[str] = []
        self.gift_min = GIFT_MIN_DEFAULT
        self._gift_seen: list[dict] = []
        self._contrib: dict[str, dict] = {}
        self._contrib_offset = 0
        self._online_text = "--"
        self._gain_text = "--"
        self._payers_text = "--"
        self._gift_total_diamonds = 0
        self.chat_rows: list[dict] = []
        self._photo_cache: dict[str, tk.PhotoImage] = {}
        self._rich_cache: dict[tuple, tk.PhotoImage] = {}
        self._chat_scroll_pending = False
        self._pv_text = ""
        self._online_text = "--"
        self._last_auto_export_events = -1
        self._auto_export_job = None
        self._auto_exported_on_end = False
        self.site_server = None
        self._site_server_dir: Path | None = None
        self.login_thread: threading.Thread | None = None
        self.login_cancel: threading.Event | None = None

        self._setup_style()
        self._build_bottombar()
        self._build_shell()

        self.root.after(60, self._dark_titlebar)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.drain_events)
        self.root.after(400, self._announce_cookie_state)
        self.root.after(120_000, self._auto_export_loop)

    def _dark_titlebar(self):
        try:
            import ctypes

            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            value = ctypes.c_int(1)
            for attr in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 样式
    # ------------------------------------------------------------------
    def _setup_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Aurora.Treeview",
            background=CARD,
            fieldbackground=CARD,
            foreground=FG,
            borderwidth=0,
            rowheight=38,
            font=(FONT, 10),
        )
        style.configure(
            "Aurora.Treeview.Heading",
            background="#151a25",
            foreground="#9aa6ba",
            borderwidth=0,
            relief="flat",
            font=(FONT, 9, "bold"),
            padding=(8, 8),
        )
        style.map(
            "Aurora.Treeview",
            background=[("selected", "#26324a")],
            foreground=[("selected", "#ffffff")],
        )
        style.map(
            "Aurora.Treeview.Heading",
            background=[("active", "#1b2230")],
        )

    # ------------------------------------------------------------------
    # 70 / 30 主布局
    # ------------------------------------------------------------------
    def _build_shell(self):
        self.shell = tk.Frame(self.root, bg=BG)
        self.shell.pack(fill="both", expand=True, padx=14, pady=(12, 4))
        self.shell.grid_rowconfigure(0, weight=1)
        self.shell.grid_columnconfigure(0, weight=3)
        self.shell.grid_columnconfigure(1, weight=1)

        self.left_col = tk.Frame(self.shell, bg=BG)
        self.left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self.right_col = tk.Frame(self.shell, bg=BG)
        self.right_col.grid(row=0, column=1, sticky="nsew", padx=(7, 0))

        self._build_chat_panel(self.left_col)
        self._build_sidebar(self.right_col)

    def _build_chat_panel(self, parent):
        panel = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        panel.pack(fill="both", expand=True)

        head = tk.Frame(panel, bg=CARD)
        head.pack(fill="x", padx=18, pady=(15, 8))
        tk.Frame(head, bg=ACCENT, width=3, height=21).pack(side="left", padx=(0, 9))
        tk.Label(head, text="弹幕", bg=CARD, fg=FG, font=(FONT, 20, "bold")).pack(side="left")
        self.header_stats_var = tk.StringVar(
            value="在线人数 --        本场涨粉 --        付费人数 --"
        )
        self.header_stats = tk.Label(
            head, textvariable=self.header_stats_var, bg=CARD, fg=FG,
            font=(FONT, 20, "bold"),
        )
        self.header_stats.place(relx=0.5, rely=0.5, anchor="center")
        self.chat_badge = tk.Label(
            head, text="0", bg="#1a2030", fg="#aeb9cc", font=(FONT, 12, "bold"),
            padx=9, pady=2,
        )
        self.chat_badge.pack(side="right")

        legend = tk.Frame(panel, bg=CARD)
        legend.pack(fill="x", padx=18, pady=(0, 8))
        tk.Label(
            legend,
            text="消费 35+ / 粉丝 15+",
            bg=CARD, fg=GOLD, font=(FONT, 13),
        ).pack(side="left")
        tk.Label(
            legend,
            text="消费 40+ / 粉丝 18+",
            bg=CARD, fg="#ff8d9b", font=(FONT, 13),
        ).pack(side="left", padx=(16, 0))
        tk.Label(
            legend,
            text="入场 15+",
            bg=CARD, fg=CYAN, font=(FONT, 13),
        ).pack(side="left", padx=(16, 0))
        tk.Label(
            legend,
            text="房管",
            bg=CARD, fg=ADMIN_BAR, font=(FONT, 13, "bold"),
        ).pack(side="left", padx=(16, 0))

        body = tk.Frame(panel, bg=CARD)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 0))
        self.chat_canvas = tk.Canvas(
            body, bg=CARD, highlightthickness=0, borderwidth=0, yscrollincrement=30
        )
        self.chat_scroll = None  # 无滚动条：滚轮 + 拖动
        self.chat_canvas.pack(side="left", fill="both", expand=True)
        self.chat_list = tk.Frame(self.chat_canvas, bg=CARD)
        self.chat_window = self.chat_canvas.create_window(
            (0, 0), window=self.chat_list, anchor="nw"
        )
        self.chat_canvas.bind("<Configure>", self._chat_canvas_resize)
        self.chat_list.bind("<Configure>", self._sync_chat_scroll)
        self.chat_canvas.bind("<MouseWheel>", self._on_chat_wheel)
        self.chat_list.bind("<MouseWheel>", self._on_chat_wheel)
        self.chat_rows = []

        self.chat_placeholder = tk.Label(
            body, text="等待弹幕…", bg=CARD, fg="#647086", font=(FONT, 15)
        )
        self.chat_placeholder.place(relx=0.5, rely=0.5, anchor="center")

        foot = tk.Frame(panel, bg=CARD)
        foot.pack(fill="x", padx=18, pady=(5, 9))
        tk.Label(
            foot, text="双击弹幕可复制完整内容", bg=CARD, fg="#657187", font=(FONT, 11)
        ).pack(side="left")
        tk.Label(
            foot, text="仅显示实时消息", bg=CARD, fg="#657187", font=(FONT, 11)
        ).pack(side="right")

    def _build_sidebar(self, parent):
        # 品牌
        brand = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        brand.pack(fill="x", pady=(0, 8))
        logo = tk.Label(
            brand, text="弹", bg=ACCENT, fg="#fff", font=(FONT, 17, "bold"),
            width=2, height=1,
        )
        logo.pack(side="left", padx=(13, 10), pady=11)
        brand_text = tk.Frame(brand, bg=CARD)
        brand_text.pack(side="left", pady=9)
        tk.Label(brand_text, text=APP_NAME, bg=CARD, fg=FG, font=(FONT, 18, "bold")).pack(anchor="w")
        tk.Label(
            brand_text, text="AURORA DARK · LIVE", bg=CARD, fg=MUTED, font=(FONT, 9)
        ).pack(anchor="w", pady=(1, 0))

        # 房间 + 连接状态
        room = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        room.pack(fill="x", pady=(0, 8))
        status_line = tk.Frame(room, bg=CARD)
        status_line.pack(fill="x", padx=12, pady=(10, 7))
        self.status_dot = tk.Canvas(status_line, width=9, height=9, bg=CARD, highlightthickness=0)
        self.status_dot.pack(side="left", padx=(0, 6))
        self._dot = self.status_dot.create_oval(1, 1, 8, 8, fill=MUTED, outline="")
        self.status_var = tk.StringVar(value="未连接")
        tk.Label(
            status_line, textvariable=self.status_var, bg=CARD, fg=MUTED, font=(FONT, 10)
        ).pack(side="left")
        self.online_top_var = tk.StringVar(value="在线 --")
        tk.Label(
            status_line, textvariable=self.online_top_var, bg=CARD, fg=GREEN,
            font=(FONT, 11, "bold"),
        ).pack(side="left", padx=(10, 0))

        self.room_var = tk.StringVar(value="https://live.douyin.com/32063570834")
        room_wrap = tk.Frame(room, bg=BORDER)
        room_wrap.pack(fill="x", padx=12)
        room_entry = tk.Entry(
            room_wrap, textvariable=self.room_var, bg=FIELD, fg=FG,
            insertbackground=FG, relief="flat", font=(FONT, 10),
        )
        room_entry.pack(fill="x", ipady=7, padx=1, pady=1)
        room_entry.bind("<Return>", lambda _e: self.connect())

        actions = tk.Frame(room, bg=CARD)
        actions.pack(fill="x", padx=12, pady=(7, 11))
        self.connect_btn = tk.Button(
            actions, text="连接", command=self.connect, bg=ACCENT, fg="#ffffff",
            activebackground=ACCENT_DIM, activeforeground="#ffffff", relief="flat",
            bd=0, font=(FONT, 11, "bold"), padx=12, pady=7, cursor="hand2",
            disabledforeground="#e9e4ff",
        )
        self.connect_btn.pack(side="left", fill="x", expand=True)
        self.disconnect_btn = tk.Button(
            actions, text="断开", command=self.disconnect, bg="#1a2030", fg=MUTED,
            activebackground="#222a3c", activeforeground=FG, relief="flat", bd=0,
            font=(FONT, 10), padx=8, pady=7, cursor="hand2", state="disabled",
        )
        self.disconnect_btn.pack(side="left", fill="x", expand=True, padx=(5, 0))
        self.export_btn = tk.Button(
            actions, text="导出", command=self.export_site, bg="#1a2030", fg="#c9d3e5",
            activebackground="#222a3c", activeforeground=FG, relief="flat", bd=0,
            font=(FONT, 10), padx=8, pady=7, cursor="hand2",
        )
        self.export_btn.pack(side="left", fill="x", expand=True, padx=(5, 0))

        login_row = tk.Frame(room, bg=CARD)
        login_row.pack(fill="x", padx=12, pady=(0, 11))
        self.login_btn = tk.Button(
            login_row, text="登录抖音", command=self._login_douyin, bg="#261b42",
            fg="#e2d6ff", activebackground="#33245a", activeforeground="#ffffff",
            relief="flat", bd=0, font=(FONT, 10, "bold"), padx=8, pady=5,
            cursor="hand2", highlightbackground="#4b3a7a", highlightthickness=1,
        )
        self.login_btn.pack(side="left", fill="x", expand=True)
        self.clear_login_btn = tk.Button(
            login_row, text="清除登录", command=self._clear_login, bg="#1a2030",
            fg="#8f9aae", activebackground="#242c3f", activeforeground=FG,
            relief="flat", bd=0, font=(FONT, 9), padx=8, pady=5, cursor="hand2",
        )
        self.clear_login_btn.pack(side="left", padx=(5, 0))

        # 汇总变量（数值进弹幕区顶部那一行，卡片不再显示）
        self.summary_vars: dict[str, tk.StringVar] = {
            key: tk.StringVar(value="--") for key in ("online", "gain", "payers", "gift")
        }
        self.payer_detail = tk.StringVar(value="")

        # 礼物贡献榜（本场）
        top_card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        top_card.pack(fill="x", pady=(0, 8))
        top_head = tk.Frame(top_card, bg=CARD)
        top_head.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(
            top_head, text="GIFT TOP", bg=CARD, fg=MAGENTA, font=(FONT, 9, "bold")
        ).pack(side="left")
        tk.Label(
            top_head, text="礼物贡献 · 本场", bg=CARD, fg=FG, font=(FONT, 14, "bold")
        ).pack(side="left", padx=(6, 0))
        self.contrib_badge = tk.Label(
            top_head, text="0", bg="#2a1020", fg="#ffb4d6", font=(FONT, 9, "bold"),
            padx=7, pady=1,
        )
        self.contrib_badge.pack(side="right")

        self.contrib_body = tk.Frame(top_card, bg=CARD)
        self.contrib_body.pack(fill="x", padx=10)
        self.contrib_cells = []
        for _ in range(CONTRIB_VISIBLE):
            row = tk.Frame(self.contrib_body, bg=CARD)
            row.pack(fill="x", pady=1)
            rank = tk.Label(row, text="", bg=CARD, fg=MUTED, font=(FONT, 12, "bold"), width=2, anchor="w")
            rank.pack(side="left")
            name = tk.Label(row, text="", bg=CARD, fg=FG, font=(FONT, 12, "bold"), anchor="w")
            name.pack(side="left", padx=(2, 0))
            value = tk.Label(row, text="", bg=CARD, fg=GOLD, font=(FONT, 12, "bold"), anchor="e")
            value.pack(side="right")
            self.contrib_cells.append((row, rank, name, value))

        tk.Label(
            top_card, text="滚轮翻看 · 保留前 50", bg=CARD, fg="#657187", font=(FONT, 9)
        ).pack(anchor="w", padx=12, pady=(2, 8))
        def _bind_wheel(widget):
            widget.bind("<MouseWheel>", self._on_contrib_wheel)
            for child in widget.winfo_children():
                _bind_wheel(child)

        _bind_wheel(top_card)

        # 礼物
        gift_card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        gift_card.pack(fill="both", expand=True)
        gift_head = tk.Frame(gift_card, bg=CARD)
        gift_head.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(
            gift_head, text="GIFT FEED", bg=CARD, fg=MAGENTA, font=(FONT, 9, "bold")
        ).pack(side="left")
        tk.Label(
            gift_head, text="礼物 · 本场", bg=CARD, fg=FG, font=(FONT, 14, "bold")
        ).pack(side="left", padx=(6, 0))
        self.gift_badge = tk.Label(
            gift_head, text="0", bg="#2a1020", fg="#ffb4d6", font=(FONT, 9, "bold"),
            padx=7, pady=1,
        )
        self.gift_badge.pack(side="right")

        th_box = tk.Frame(gift_head, bg=CARD)
        th_box.pack(side="right", padx=(0, 8))
        tk.Label(th_box, text="≥", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left")
        self.gift_min_var = tk.StringVar(value=str(GIFT_MIN_DEFAULT))
        self.gift_min_entry = tk.Entry(
            th_box,
            textvariable=self.gift_min_var,
            width=4,
            justify="center",
            bg=FIELD,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=(FONT, 9, "bold"),
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        self.gift_min_entry.pack(side="left", padx=2, ipady=1)
        tk.Label(th_box, text="钻", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left")
        self.gift_min_entry.bind("<Return>", lambda _e: self._apply_gift_min())
        self.gift_min_entry.bind("<FocusOut>", lambda _e: self._apply_gift_min())
        self.gift_min_entry.bind("<KP_Enter>", lambda _e: self._apply_gift_min())

        # 底部说明行已移除（礼物行铺满卡片），变量保留给内部刷新逻辑
        self.gift_foot_var = tk.StringVar(value="")

        gift_body = tk.Frame(gift_card, bg=CARD)
        gift_body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.gift_canvas = tk.Canvas(
            gift_body, bg=CARD, height=384, highlightthickness=0, borderwidth=0, yscrollincrement=30
        )
        self.gift_scroll = None  # 无滚动条：滚轮 + 拖动
        self.gift_canvas.pack(side="left", fill="both", expand=True)
        self.gift_list = tk.Frame(self.gift_canvas, bg=CARD)
        self.gift_window = self.gift_canvas.create_window(
            (0, 0), window=self.gift_list, anchor="nw"
        )
        self.gift_canvas.bind("<Configure>", self._sync_gift_canvas)
        self.gift_list.bind("<Configure>", self._sync_gift_scroll)
        self.gift_canvas.bind("<MouseWheel>", self._on_gift_wheel)
        self.gift_list.bind("<MouseWheel>", self._on_gift_wheel)
        self.gift_placeholder = None
        self._show_gift_placeholder()

    def _build_bottombar(self):
        bar = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        bar.pack(side="bottom", fill="x", padx=14, pady=(0, 8))
        row = tk.Frame(bar, bg=CARD)
        row.pack(fill="x", padx=10, pady=6)
        self.cookie_var = tk.StringVar()
        self.cookie_hint_var = tk.StringVar(value="未登录抖音：礼物消息不会推送")
        self.cookie_status_label = tk.Label(
            row, textvariable=self.cookie_hint_var, bg=CARD, fg="#ff7b8a",
            font=(FONT, 10, "bold"),
        )
        self.cookie_status_label.pack(side="left")
        self.hint_var = tk.StringVar(value="等待连接")
        tk.Label(
            row, textvariable=self.hint_var, bg=CARD, fg="#657187", font=(FONT, 9)
        ).pack(side="left", padx=(14, 0))
        self.csv_var = tk.StringVar(value="")
        tk.Label(
            row, textvariable=self.csv_var, bg=CARD, fg="#596579", font=(FONT, 8)
        ).pack(side="right")

        if (app_dir() / "cookie.txt").exists():
            self._load_cookie_file(silent=True)

    def _chat_canvas_resize(self, event=None):
        try:
            self.chat_canvas.itemconfigure(
                self.chat_window, width=max(1, self.chat_canvas.winfo_width())
            )
        except Exception:
            pass
        width = self.chat_canvas.winfo_width()
        for row in self.chat_rows:
            try:
                nick_w = row["nick"].winfo_reqwidth() if row.get("nick") else 0
                wrap = max(180, width - row.get("lead", 200) - nick_w - 24)
                if row.get("rich"):
                    photo = self._rich_photo(
                        row.get("text") or "", row.get("fg") or FG, wrap
                    )
                    if photo is not None:
                        row["content"].configure(image=photo)
                        row["content"].image = photo
                        continue
                row["content"].configure(wraplength=wrap)
            except Exception:
                pass
        self._sync_chat_scroll()

    def _sync_chat_scroll(self, event=None):
        try:
            self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox("all"))
        except Exception:
            pass

    def _on_chat_wheel(self, event):
        self.chat_canvas.yview_scroll(-3 if event.delta > 0 else 3, "units")
        return "break"

    def _chat_bg(self, tags) -> tuple[str, str, str]:
        tags = tuple(tags or ())
        if "admin" in tags:
            return ADMIN_BG, ADMIN_NAME, "admin"
        if "super" in tags:
            return "#2e1218", "#ffd7e5", "super"
        if "hot" in tags:
            return "#2a2312", "#ffe5a6", "hot"
        if "entry" in tags:
            return "#062d36", "#bdf8ff", "entry"
        return (CARD if self.chat_count % 2 == 0 else "#0d1119"), "#dfe5f4", "normal"

    def _photo(self, name: str) -> tk.PhotoImage | None:
        cached = self._photo_cache.get(name)
        if cached is not None:
            return cached
        path = resource_dir() / "assets" / "ui_badges" / name
        try:
            im = tk.PhotoImage(file=str(path))
        except Exception:
            return None
        self._photo_cache[name] = im
        return im

    def _emoji_photo(self, path: str) -> tk.PhotoImage | None:
        """会员表情：等比缩到高度正好 44px（Tk 只能整数 subsample，这里用 PIL 预缩）。"""
        key = f"emoji:{path}"
        cached = self._photo_cache.get(key)
        if cached is not None:
            return cached
        target_h = 44
        try:
            src = Image.open(path).convert("RGBA")
            if src.height != target_h:
                target_w = max(1, round(src.width * target_h / src.height))
                out_dir = os.path.join(os.path.dirname(str(path)), "_44")
                os.makedirs(out_dir, exist_ok=True)
                small = os.path.join(
                    out_dir, f"{os.path.splitext(os.path.basename(str(path)))[0]}_{target_h}.png"
                )
                if not os.path.exists(small):
                    src.resize((target_w, target_h), Image.LANCZOS).save(small)
                im = tk.PhotoImage(file=small)
            else:
                im = tk.PhotoImage(file=str(path))
        except Exception:
            return None
        self._photo_cache[key] = im
        return im

    def _rich_photo(self, text: str, color: str, max_w: int):
        key = (text, color, int(max_w) // 8)
        cached = self._rich_cache.get(key)
        if cached is not None:
            return cached
        try:
            im = render_rich_content(text, color, max_w)
        except Exception:
            im = None
        if im is None:
            return None
        try:
            from PIL import ImageTk

            photo = ImageTk.PhotoImage(im)
        except Exception:
            return None
        if len(self._rich_cache) > 400:
            self._rich_cache.clear()
        self._rich_cache[key] = photo
        return photo

    def _chat_badge_names(self, event: dict) -> list[str]:
        names: list[str] = []
        try:
            pay = int(event.get("pay_level") or 0)
            level = int(event.get("fans_club_level") or 0)
        except (TypeError, ValueError):
            pay = level = 0
        if pay > 0:
            names.append(f"honor_{min(75, pay)}.png")
        if level > 0:
            names.append(f"club_{min(20, level)}.png")
        star = admin = False
        for item in event.get("badge_items") or []:
            if not isinstance(item, dict):
                continue
            who = _badge_identity(
                str(item.get("label") or ""),
                str(item.get("url") or ""),
                str(item.get("uri") or ""),
            )
            if who == "房管":
                admin = True
            elif who == "星守护":
                star = True
        if star:
            names.append("mark_star.png")
        if admin:
            names.append("mark_admin.png")
        return names

    def _add_chat_row(self, values, tags, event=None):
        bg, fg, kind = self._chat_bg(tags)
        body_fg = ADMIN_BODY if kind == "admin" else fg
        event = event or {}
        nick, content = values
        frame = tk.Frame(self.chat_list, bg=bg)
        frame.pack(fill="x")
        if kind == "admin":
            tk.Frame(frame, bg=ADMIN_BAR, width=4).place(x=0, y=0, relheight=1)
        frame.grid_columnconfigure(3, weight=1)

        badge_box = tk.Frame(frame, bg=bg)
        badge_box.grid(row=0, column=0, sticky="nw", padx=(10, 0), pady=(14, 2))
        badge_w = 0
        for idx, name in enumerate(self._chat_badge_names(event)):
            im = self._photo(name)
            if im is None:
                continue
            bl = tk.Label(badge_box, image=im, bg=bg, bd=0, highlightthickness=0)
            bl.pack(side="left", padx=(0 if idx == 0 else 2, 0))
            badge_w += im.width() + (0 if idx == 0 else 2)
            bl.bind("<Double-1>", lambda _e, t=content: self._copy_chat(text=t))
            bl.bind("<MouseWheel>", self._on_chat_wheel, add="+")

        n = tk.Label(frame, text=nick, bg=bg, fg=fg, font=(FONT, 18, "bold"), anchor="w")
        n.grid(row=0, column=1, sticky="nw", padx=(6, 0), pady=8)
        colon = tk.Label(
            frame, text="：", bg=bg, fg="#8792a6", font=(FONT, 18, "bold"), anchor="w"
        )
        colon.grid(row=0, column=2, sticky="nw", padx=(0, 2), pady=8)
        wrap = max(200, self.chat_canvas.winfo_width() - badge_w - 236)
        rich = self._rich_photo(content, body_fg, wrap) if has_dy_emoji(content) else None
        if rich is not None:
            c = tk.Label(frame, image=rich, bg=bg, bd=0, anchor="w")
            c.image = rich
        else:
            c = tk.Label(
                frame, text=content, bg=bg, fg=body_fg, font=(FONT, 18),
                anchor="w", justify="left", wraplength=wrap,
            )
        c.grid(row=0, column=3, sticky="nsew", padx=(0, 10), pady=8)
        self._bind_drag(frame, self.chat_canvas)

        emoji_path = str(event.get("emoji_path") or "")
        if emoji_path:
            em = self._emoji_photo(emoji_path)
            if em is not None:
                # 会员表情永远单独出现：去掉占位文字，直接把图放在冒号后
                c.configure(text="", image=em, wraplength=0, pady=6)
                c.image = em

        info = {
            "frame": frame,
            "content": c,
            "nick": n,
            "text": content,
            "fg": fg,
            "rich": rich is not None,
            "lead": badge_w + 30,
        }
        self.chat_rows.append(info)
        for widget in (frame, n, colon, c):
            widget.bind("<Double-1>", lambda _e, t=content: self._copy_chat(text=t))
            widget.bind("<MouseWheel>", self._on_chat_wheel, add="+")

        while len(self.chat_rows) > CHAT_LIMIT:
            old = self.chat_rows.pop(0)
            old["frame"].destroy()

        self.chat_count += 1
        self.chat_badge.configure(text=fmt_int(self.chat_count))
        self._hide_chat_placeholder()
        self._schedule_chat_scroll()

    def _schedule_chat_scroll(self):
        if self._chat_scroll_pending:
            return
        self._chat_scroll_pending = True
        self.root.after(120, self._scroll_chat_to_bottom)

    def _scroll_chat_to_bottom(self):
        self._chat_scroll_pending = False
        self.chat_canvas.update_idletasks()
        self._sync_chat_scroll()
        self.chat_canvas.yview_moveto(1.0)

    # ------------------------------------------------------------------
    # 顶栏
    # ------------------------------------------------------------------
    def _build_topbar(self):
        bar = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        bar.pack(fill="x", padx=18, pady=(14, 8))

        logo = tk.Label(
            bar,
            text="弹",
            bg=ACCENT,
            fg="#ffffff",
            font=(FONT, 15, "bold"),
            width=2,
            height=1,
        )
        logo.pack(side="left", padx=(14, 10), pady=11)

        brand = tk.Frame(bar, bg=CARD)
        brand.pack(side="left", pady=10)
        tk.Label(
            brand, text=APP_NAME, bg=CARD, fg=FG, font=(FONT, 18, "bold")
        ).pack(anchor="w")
        tk.Label(
            brand,
            text="抖音直播 · 弹幕 / 入场 / 礼物",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 8),
        ).pack(anchor="w", pady=(1, 0))

        right = tk.Frame(bar, bg=CARD)
        right.pack(side="right", padx=(0, 14))
        self.status_dot = tk.Canvas(right, width=9, height=9, bg=CARD, highlightthickness=0)
        self.status_dot.pack(side="left", padx=(0, 6))
        self._dot = self.status_dot.create_oval(1, 1, 8, 8, fill=MUTED, outline="")
        self.status_var = tk.StringVar(value="未连接")
        tk.Label(
            right, textvariable=self.status_var, bg=CARD, fg=MUTED, font=(FONT, 9)
        ).pack(side="left")
        self.online_top_var = tk.StringVar(value="在线 --")
        tk.Label(
            right,
            textvariable=self.online_top_var,
            bg=CARD,
            fg=GREEN,
            font=(FONT, 10, "bold"),
        ).pack(side="left", padx=(18, 0))

        controls = tk.Frame(bar, bg=CARD)
        controls.pack(side="right", padx=(0, 20))
        self.room_var = tk.StringVar(value="https://live.douyin.com/617650815017")

        room_wrap = tk.Frame(controls, bg=BORDER)
        room_wrap.pack(side="left", padx=(0, 8))
        room_entry = tk.Entry(
            room_wrap,
            textvariable=self.room_var,
            bg=FIELD,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=(FONT, 9),
            width=38,
        )
        room_entry.pack(ipady=8, padx=1, pady=1)
        room_entry.bind("<Return>", lambda _e: self.connect())

        self.connect_btn = tk.Button(
            controls,
            text="连接",
            command=self.connect,
            bg=ACCENT,
            fg="#ffffff",
            activebackground=ACCENT_DIM,
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            font=(FONT, 10, "bold"),
            padx=17,
            pady=8,
            cursor="hand2",
            disabledforeground="#d9d0ff",
        )
        self.connect_btn.pack(side="left", padx=(0, 6))

        self.disconnect_btn = tk.Button(
            controls,
            text="断开",
            command=self.disconnect,
            bg="#1a2030",
            fg=MUTED,
            activebackground="#222a3c",
            activeforeground=FG,
            relief="flat",
            bd=0,
            font=(FONT, 9),
            padx=13,
            pady=8,
            cursor="hand2",
            state="disabled",
        )
        self.disconnect_btn.pack(side="left")

        self.export_btn = tk.Button(
            controls,
            text="导出网站",
            command=self.export_site,
            bg="#1a2030",
            fg="#c9d3e5",
            activebackground="#222a3c",
            activeforeground=FG,
            relief="flat",
            bd=0,
            font=(FONT, 9),
            padx=12,
            pady=8,
            cursor="hand2",
        )
        self.export_btn.pack(side="left", padx=(6, 0))

    # ------------------------------------------------------------------
    # Cookie
    # ------------------------------------------------------------------
    def _build_cookiebar(self):
        bar = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        bar.pack(fill="x", padx=18, pady=(0, 8))

        tk.Label(
            bar, text="Cookie", bg=CARD, fg=MUTED, font=(FONT, 8, "bold")
        ).pack(side="left", padx=(14, 0))
        tk.Label(
            bar,
            text="登录抖音网页版后复制；填了才能收到礼物消息",
            bg=CARD,
            fg="#778399",
            font=(FONT, 8),
        ).pack(side="left", padx=(6, 8))

        self.cookie_var = tk.StringVar()
        wrap = tk.Frame(bar, bg=BORDER)
        wrap.pack(side="left", fill="x", expand=True, pady=5)
        self.cookie_entry = tk.Entry(
            wrap,
            textvariable=self.cookie_var,
            bg=FIELD,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=(FONT, 8),
            show="•",
        )
        self.cookie_entry.pack(fill="x", ipady=4, padx=1, pady=1)

        for text, command in (
            ("读取 cookie.txt", self._load_cookie_file),
            ("保存", self._save_cookie_file),
            ("清除", self._clear_cookie),
        ):
            tk.Button(
                bar,
                text=text,
                command=command,
                bg="#1a2030",
                fg="#b8c3d6",
                activebackground="#222a3c",
                activeforeground=FG,
                relief="flat",
                bd=0,
                font=(FONT, 8),
                padx=9,
                pady=4,
                cursor="hand2",
            ).pack(side="left", padx=(5, 0), pady=5)

        self.cookie_hint_var = tk.StringVar(value="未填 Cookie：只能收弹幕")
        tk.Label(
            bar,
            textvariable=self.cookie_hint_var,
            bg=CARD,
            fg="#778399",
            font=(FONT, 8),
        ).pack(side="left", padx=(8, 12))

        if (app_dir() / "cookie.txt").exists():
            self._load_cookie_file(silent=True)

    def _load_cookie_file(self, silent: bool = False):
        path = app_dir() / "cookie.txt"
        if not path.exists():
            if not silent:
                messagebox.showinfo(
                    "Cookie",
                    "没有找到 cookie.txt。\n\n"
                    "在程序目录新建 cookie.txt，把浏览器复制的 Cookie 粘进去即可。",
                )
            return
        try:
            cookie = path.read_text(encoding="utf-8-sig").strip()
        except Exception as exc:
            if not silent:
                messagebox.showerror("Cookie", f"读取失败：{exc}")
            return
        cookie = cookie.strip().strip('"').strip("'")
        if cookie.lower().startswith("cookie:"):
            cookie = cookie[7:].strip()
        self.cookie_var.set(cookie)
        self._refresh_cookie_status()

    def _cookie_age_days(self) -> float | None:
        path = app_dir() / "cookie.txt"
        try:
            return max(0.0, (time.time() - path.stat().st_mtime) / 86400.0)
        except Exception:
            return None

    def _refresh_cookie_status(self):
        cookie = self.cookie_var.get().strip()
        path = app_dir() / "cookie.txt"
        if not cookie or not path.exists():
            text = "未登录抖音：礼物消息不会推送"
            color = RED
        else:
            age = self._cookie_age_days()
            if age is not None and age >= 7:
                text = f"登录 Cookie 已保存 {int(age)} 天，可能已过期"
                color = GOLD
            else:
                text = "已登录抖音 · Cookie 已加载"
                color = GREEN
        if hasattr(self, "cookie_hint_var"):
            self.cookie_hint_var.set(text)
        if hasattr(self, "cookie_status_label"):
            self.cookie_status_label.configure(fg=color)

    def _announce_cookie_state(self):
        cookie = self.cookie_var.get().strip()
        path = app_dir() / "cookie.txt"
        age = self._cookie_age_days()
        if not cookie or not path.exists():
            self._show_cookie_notice(
                "当前未登录抖音。\n\n"
                "礼物消息不会推送，礼物区、礼物总额和付费人数都会是空的；"
                "弹幕、入场和在线人数仍然正常。\n\n"
                "请点右侧「登录抖音」扫码，登录成功后再点「连接」。",
                "去登录",
            )
        elif age is not None and age >= 7:
            self._show_cookie_notice(
                f"检测到本机 Cookie 已保存约 {int(age)} 天，可能已经失效。\n\n"
                "如果礼物区一直为空，请重新登录抖音。",
                "重新登录",
            )
        else:
            days = 0 if age is None else int(age)
            self._toast(
                f"已加载登录 Cookie（{days} 天前保存）· 礼物消息应可用"
            )

    def _show_cookie_notice(self, message: str, action_text: str):
        win = tk.Toplevel(self.root)
        win.title("抖音登录状态")
        win.configure(bg=CARD)
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        width, height = 560, 310
        self.root.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - height) // 2
        win.geometry(f"{width}x{height}+{x}+{y}")

        head = tk.Frame(win, bg=CARD)
        head.pack(fill="x", padx=22, pady=(18, 6))
        tk.Label(head, text="●", bg=CARD, fg=RED, font=(FONT, 13)).pack(side="left")
        tk.Label(
            head, text="抖音登录状态", bg=CARD, fg=FG, font=(FONT, 15, "bold")
        ).pack(side="left", padx=(8, 0))

        tk.Label(
            win,
            text=message,
            bg=CARD,
            fg="#dbe3f1",
            font=(FONT, 10),
            justify="left",
            anchor="w",
            wraplength=500,
        ).pack(fill="both", expand=True, padx=24, pady=(4, 8))

        buttons = tk.Frame(win, bg=CARD)
        buttons.pack(fill="x", padx=22, pady=(0, 18))
        primary = tk.Button(
            buttons,
            text=action_text,
            bg=ACCENT,
            fg="#ffffff",
            activebackground=ACCENT_DIM,
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            font=(FONT, 11, "bold"),
            padx=18,
            pady=8,
            cursor="hand2",
        )
        primary.pack(side="right")
        secondary = tk.Button(
            buttons,
            text="先只看弹幕",
            bg="#1a2030",
            fg="#b8c3d6",
            activebackground="#242c3f",
            activeforeground=FG,
            relief="flat",
            bd=0,
            font=(FONT, 10),
            padx=14,
            pady=8,
            cursor="hand2",
            command=win.destroy,
        )
        secondary.pack(side="right", padx=(0, 8))

        def go_login():
            win.destroy()
            self._login_douyin()

        primary.configure(command=go_login)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.lift()
        win.focus_force()

    def _toast(self, text: str, color: str = GREEN):
        label = tk.Label(
            self.root,
            text=text,
            bg="#14251f",
            fg=color,
            font=(FONT, 11, "bold"),
            padx=18,
            pady=8,
            bd=0,
        )
        label.place(relx=0.5, y=14, anchor="n")
        self.root.after(5000, label.destroy)

    def _clear_cookie(self):
        self.cookie_var.set("")
        self._refresh_cookie_status()

    def _save_cookie_file(self):
        cookie = self.cookie_var.get().strip()
        if not cookie:
            messagebox.showinfo("Cookie", "Cookie 是空的，没有保存。")
            return
        try:
            (app_dir() / "cookie.txt").write_text(cookie, encoding="utf-8")
            self.cookie_hint_var.set("已保存到 cookie.txt（明文，仅本机）")
        except Exception as exc:
            messagebox.showerror("Cookie", f"保存失败：{exc}")

    def _login_douyin(self):
        if self.login_thread is not None and self.login_thread.is_alive():
            return
        self.login_cancel = threading.Event()
        self.login_btn.configure(state="disabled", text="登录中…")
        self.clear_login_btn.configure(state="disabled")
        self.connect_btn.configure(state="disabled")
        self.cookie_hint_var.set("正在打开 Edge 登录窗口…")
        self.cookie_status_label.configure(fg=GOLD)
        self.hint_var.set("请在弹出的 Edge 窗口扫码登录")
        self.login_thread = threading.Thread(target=self._login_worker, daemon=True)
        self.login_thread.start()

    def _login_worker(self):
        profile = app_dir() / "login_profile"
        try:
            cookie = login_and_get_cookie(
                profile,
                status_cb=lambda text: self.events.put(
                    {"kind": "login_status", "text": text}
                ),
                cancel_event=self.login_cancel,
            )
        except BrowserLoginCancelled as exc:
            self.events.put({"kind": "login_error", "text": str(exc)})
        except BrowserLoginError as exc:
            self.events.put({"kind": "login_error", "text": str(exc)})
        except Exception as exc:
            self.events.put({"kind": "login_error", "text": f"登录失败：{exc}"})
        else:
            self.events.put({"kind": "login_done", "cookie": cookie})

    def _handle_login_event(self, event: dict):
        kind = event.get("kind")
        if kind == "login_status":
            self.cookie_hint_var.set(event.get("text") or "")
            self.cookie_status_label.configure(fg=GOLD)
            return
        self.login_btn.configure(state="normal")
        self.clear_login_btn.configure(state="normal")
        self.connect_btn.configure(state="normal")
        if kind == "login_done":
            cookie = (event.get("cookie") or "").strip()
            try:
                (app_dir() / "cookie.txt").write_text(cookie, encoding="utf-8")
            except Exception as exc:
                messagebox.showerror("登录", f"Cookie 写入失败：{exc}")
                self.login_btn.configure(text="登录抖音")
                self._refresh_cookie_status()
                return
            self.cookie_var.set(cookie)
            self.login_btn.configure(text="重新登录")
            self._refresh_cookie_status()
            self.hint_var.set("登录成功，可以连接直播间")
            self._set_status("登录成功", GREEN)
            self._toast("登录成功 · Cookie 已保存，礼物消息应可用")
        else:
            self.login_btn.configure(text="登录抖音")
            self._refresh_cookie_status()
            detail = (event.get("text") or "").strip()
            if detail:
                self.cookie_hint_var.set(detail)
                try:
                    log_dir = app_dir() / "logs"
                    log_dir.mkdir(exist_ok=True)
                    with (log_dir / "login.log").open("a", encoding="utf-8") as fh:
                        fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + detail + "\n")
                except Exception:
                    pass
            elif not self.cookie_var.get().strip():
                self.cookie_hint_var.set("未登录抖音：登录失败，请重试")
            self.cookie_status_label.configure(fg=RED)
            self._set_status("登录失败", RED)

    def _clear_login(self):
        if not messagebox.askyesno(
            "清除登录",
            "会删除本机保存的 cookie.txt 和独立 Edge 登录配置，确定吗？",
        ):
            return
        self._clear_cookie()
        try:
            (app_dir() / "cookie.txt").unlink(missing_ok=True)
        except Exception:
            pass
        clear_login_profile(app_dir() / "login_profile")
        self.login_btn.configure(text="登录抖音")
        self.hint_var.set("已清除登录，下次连接生效")
        self._set_status("未登录", MUTED)

    # ------------------------------------------------------------------
    # 顶部指标
    # ------------------------------------------------------------------
    def _build_metrics(self):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=18, pady=(0, 10))
        specs = [
            ("online", "当前在线", "人", ACCENT),
            ("gain", "本场涨粉", "人", BLUE),
            ("payers", "付费人数", "人", RED),
            ("gift", "礼物总额", "钻", GOLD),
            ("pv", "累计观看", "", MUTED),
        ]
        self.summary_vars: dict[str, tk.StringVar] = {}
        for index, (key, label, unit, color) in enumerate(specs):
            row.grid_columnconfigure(index, weight=1)
            card = tk.Frame(row, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 7, 0))
            inner = tk.Frame(card, bg=CARD)
            inner.pack(fill="both", expand=True, padx=13, pady=9)
            tk.Frame(inner, bg=color, width=25, height=3).pack(anchor="w")
            tk.Label(
                inner, text=label, bg=CARD, fg=MUTED, font=(FONT, 9)
            ).pack(anchor="w", pady=(6, 0))
            line = tk.Frame(inner, bg=CARD)
            line.pack(anchor="w", pady=(0, 0))
            var = tk.StringVar(value="--")
            self.summary_vars[key] = var
            tk.Label(
                line,
                textvariable=var,
                bg=CARD,
                fg=FG if key != "pv" else "#c5cede",
                font=(FONT, 23, "bold"),
            ).pack(side="left")
            tk.Label(
                line, text=unit, bg=CARD, fg=MUTED, font=(FONT, 9)
            ).pack(side="left", padx=(4, 0), pady=(0, 4))
            if key == "payers":
                self.payer_detail = tk.StringVar(value="")
                tk.Label(
                    inner,
                    textvariable=self.payer_detail,
                    bg=CARD,
                    fg=MUTED,
                    font=(FONT, 8),
                ).pack(anchor="w")

    # ------------------------------------------------------------------
    # 主弹幕 + 底部礼物
    # ------------------------------------------------------------------
    def _build_feed(self):
        panel = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        panel.pack(fill="both", expand=True, padx=18, pady=(0, 4))

        head = tk.Frame(panel, bg=CARD)
        head.pack(fill="x", padx=14, pady=(10, 6))
        tk.Frame(head, bg=ACCENT, width=3, height=18).pack(side="left", padx=(0, 8))
        tk.Label(
            head, text="弹幕", bg=CARD, fg=FG, font=(FONT, 14, "bold")
        ).pack(side="left")
        tk.Label(
            head,
            text="聊天 / 入场 / 礼物",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
        ).pack(side="left", padx=(8, 0), pady=(3, 0))
        self.chat_badge = tk.Label(
            head, text="0", bg="#1a2030", fg="#aeb9cc", font=(FONT, 8, "bold"),
            padx=7, pady=1,
        )
        self.chat_badge.pack(side="right")

        note = tk.Frame(panel, bg=CARD)
        note.pack(fill="x", padx=14, pady=(0, 6))
        tk.Label(
            note,
            text="入场 ≥15 青色高亮 · 礼物 ≥50 钻 洋红最高级高亮 · 弹幕 35+/15+ 金色、40+/18+ 红色",
            bg=CARD,
            fg="#778399",
            font=(FONT, 8),
        ).pack(side="left")

        tree_wrap = tk.Frame(panel, bg=CARD)
        tree_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 0))
        self.chat_tree = ttk.Treeview(
            tree_wrap,
            columns=("pay", "level", "badge", "nick", "content"),
            show="headings",
            style="Aurora.Treeview",
            selectmode="browse",
        )
        for key, label, width, anchor, stretch in (
            ("pay", "消费等级", 88, "center", False),
            ("level", "粉丝团", 78, "center", False),
            ("badge", "标识", 84, "center", False),
            ("nick", "昵称", 180, "w", False),
            ("content", "弹幕内容", 800, "w", True),
        ):
            self.chat_tree.heading(key, text=label, anchor=anchor)
            self.chat_tree.column(key, width=width, anchor=anchor, stretch=stretch, minwidth=60)
        scroll = tk.Scrollbar(
            tree_wrap,
            orient="vertical",
            command=self.chat_tree.yview,
            bg="#1c2433",
            troughcolor=CARD,
            activebackground="#2a3548",
            highlightthickness=0,
            bd=0,
            relief="flat",
            width=12,
        )
        self.chat_tree.configure(yscrollcommand=scroll.set)
        self.chat_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.chat_tree.tag_configure("odd", background=CARD)
        self.chat_tree.tag_configure("even", background="#0d1119")
        self.chat_tree.tag_configure(
            "hot",
            background="#2a2312",
            foreground="#ffd479",
            font=(FONT, 10, "bold"),
        )
        self.chat_tree.tag_configure(
            "super",
            background="#2e1218",
            foreground="#ff9db0",
            font=(FONT, 11, "bold"),
        )
        self.chat_tree.tag_configure(
            "entry",
            background="#062d36",
            foreground="#bffaff",
            font=(FONT, 10, "bold"),
        )
        self.chat_tree.bind("<Double-1>", self._copy_chat)

        self.chat_placeholder = tk.Label(
            tree_wrap,
            text="等待弹幕…",
            bg=CARD,
            fg="#647086",
            font=(FONT, 10),
        )
        self.chat_placeholder.place(relx=0.5, rely=0.5, anchor="center")

        # 礼物条固定贴在面板底部，先占位，避免被弹幕表挤出窗口。
        tree_wrap.pack_forget()
        gift_strip = tk.Frame(panel, bg=CARD)
        gift_strip.pack(side="bottom", fill="x", padx=14, pady=(8, 12))
        gift_head = tk.Frame(gift_strip, bg=CARD)
        gift_head.pack(fill="x", pady=(0, 5))
        tk.Label(
            gift_head,
            text="GIFT HIGHLIGHT",
            bg=CARD,
            fg=MAGENTA,
            font=(FONT, 8, "bold"),
        ).pack(side="left")
        tk.Label(
            gift_head,
            text="礼物 · 高价值记录",
            bg=CARD,
            fg=FG,
            font=(FONT, 12, "bold"),
        ).pack(side="left", padx=(8, 0))
        tk.Label(
            gift_head,
            text="仅显示整组累计 ≥50 钻 · 滚轮回看",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 8),
        ).pack(side="right")
        self.gift_badge = tk.Label(
            gift_head, text="0", bg="#2a1020", fg="#ffb4d6",
            font=(FONT, 8, "bold"), padx=7, pady=1,
        )
        self.gift_badge.pack(side="right", padx=(0, 10))

        gift_body = tk.Frame(gift_strip, bg=CARD)
        gift_body.pack(fill="x")
        self.gift_canvas = tk.Canvas(
            gift_body,
            bg=CARD,
            height=220,
            highlightthickness=0,
            borderwidth=0,
            yscrollincrement=28,
        )
        self.gift_scroll = tk.Scrollbar(
            gift_body,
            orient="vertical",
            command=self.gift_canvas.yview,
            width=10,
            bg="#241522",
            troughcolor="#0c1018",
            activebackground=MAGENTA,
            relief="flat",
            borderwidth=0,
        )
        self.gift_canvas.configure(yscrollcommand=self.gift_scroll.set)
        self.gift_scroll.pack(side="right", fill="y")
        self.gift_canvas.pack(side="left", fill="both", expand=True)
        self.gift_list = tk.Frame(self.gift_canvas, bg=CARD)
        self.gift_window = self.gift_canvas.create_window(
            (0, 0), window=self.gift_list, anchor="nw"
        )
        self.gift_canvas.bind("<Configure>", self._sync_gift_canvas)
        self.gift_list.bind("<Configure>", self._sync_gift_scroll)
        self.gift_canvas.bind("<MouseWheel>", self._on_gift_wheel)
        self.gift_list.bind("<MouseWheel>", self._on_gift_wheel)
        self.gift_placeholder = None
        self._show_gift_placeholder()
        tree_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 0))

    def _sync_gift_canvas(self, event=None):
        try:
            self.gift_canvas.itemconfigure(
                self.gift_window, width=max(1, self.gift_canvas.winfo_width())
            )
        except Exception:
            pass
        self._fit_gift_rows()
        self._sync_gift_scroll()

    def _fit_gift_rows(self):
        """Keep the eight gift rows stretched so they fill the card."""
        try:
            height = self.gift_canvas.winfo_height()
        except Exception:
            return
        if height <= 1:
            return
        row_h = max(38, height // 8)
        for info in self.gift_rows.values():
            try:
                info["frame"].configure(height=row_h)
            except Exception:
                pass

    def _sync_gift_scroll(self, event=None):
        try:
            self.gift_canvas.configure(scrollregion=self.gift_canvas.bbox("all"))
        except Exception:
            pass

    def _on_gift_wheel(self, event):
        step = -3 if event.delta > 0 else 3
        self.gift_canvas.yview_scroll(step, "units")
        return "break"

    def _drag_press(self, event, canvas):
        self._drag_canvas = canvas
        self._drag_y = event.y_root
        self.root.bind_all("<B1-Motion>", self._drag_motion)
        self.root.bind_all("<ButtonRelease-1>", self._drag_release)
        return None

    def _drag_motion(self, event):
        canvas = getattr(self, "_drag_canvas", None)
        if canvas is None:
            return None
        dy = event.y_root - getattr(self, "_drag_y", event.y_root)
        self._drag_y = event.y_root
        if abs(dy) >= 2:
            step = int(round(dy / 4.0))
            canvas.yview_scroll(-step if step else (-1 if dy > 0 else 1), "units")
        return None

    def _drag_release(self, event=None):
        self.root.unbind_all("<B1-Motion>")
        self.root.unbind_all("<ButtonRelease-1>")
        self._drag_canvas = None
        return None

    def _bind_drag(self, widget, canvas):
        widget.bind("<ButtonPress-1>", lambda e, c=canvas: self._drag_press(e, c), add="+")
        for child in widget.winfo_children():
            self._bind_drag(child, canvas)

    def _bind_gift_wheel(self, widget):
        widget.bind("<MouseWheel>", self._on_gift_wheel, add="+")
        for child in widget.winfo_children():
            self._bind_gift_wheel(child)

    def _show_gift_placeholder(self):
        if self.gift_placeholder is not None:
            return
        self.gift_placeholder = tk.Label(
            self.gift_list,
            text="高价值礼物会显示在这里",
            bg=CARD,
            fg="#647086",
            font=(FONT, 9),
            pady=12,
        )
        self.gift_placeholder.pack(pady=18)
        self._bind_gift_wheel(self.gift_placeholder)

    def _hide_gift_placeholder(self):
        if self.gift_placeholder is not None:
            self.gift_placeholder.destroy()
            self.gift_placeholder = None

    def _gift_row(self, event: dict) -> dict:
        key = str(event.get("group_key") or event.get("msg_id") or "")
        bg = "#170d16"
        row = tk.Frame(
            self.gift_list,
            bg=bg,
            height=44,
            highlightbackground="#4a1d35",
            highlightthickness=1,
        )
        row.pack(fill="x", pady=0)
        row.pack_propagate(False)
        row.grid_columnconfigure(0, weight=1)
        left = tk.Frame(row, bg=bg)
        left.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=2)
        left.grid_columnconfigure(2, weight=1)

        nick = tk.Label(
            left,
            text="",
            bg=bg,
            fg="#ffc2d6",
            font=(FONT, 17, "bold"),
            anchor="w",
        )
        nick.grid(row=0, column=0, sticky="w")
        pre = tk.Label(left, text="送出了", bg=bg, fg="#7d889c", font=(FONT, 9))
        pre.grid(row=0, column=1, sticky="w", padx=(6, 6))
        gift = tk.Label(
            left,
            text="",
            bg=bg,
            fg="#ffd76a",
            font=(FONT, 17, "bold"),
            anchor="w",
        )
        gift.grid(row=0, column=2, sticky="w")
        mult = tk.Label(
            row,
            text="",
            bg="#2b1a0c",
            fg=GOLD,
            font=(FONT, 15, "bold"),
            anchor="e",
            padx=8,
            pady=2,
        )
        mult.grid(row=0, column=1, sticky="e", padx=(4, 6), pady=2)

        self._bind_gift_wheel(row)
        self._bind_drag(row, self.gift_canvas)
        return {
            "key": key,
            "frame": row,
            "nick": nick,
            "gift": gift,
            "mult": mult,
        }

    def _update_gift_row(self, info: dict, event: dict):
        count = int(event.get("gift_count") or 0)
        nickname = str(event.get("nickname") or "未记录昵称")
        if len(nickname) > 10:
            nickname = nickname[:9] + "…"
        info["nick"].configure(text=nickname)
        info["gift"].configure(text=self._fit_gift_text(event.get('gift_name', '')))
        info["mult"].configure(
            text=f"×{count}  价值{fmt_int(event.get('diamonds', 0))}钻"
        )

    def _fit_gift_text(self, name: str) -> str:
        """礼物名太长时截断成「【名字…】」，避免被右侧钻数药丸压住。"""
        txt = f"【{name}】"
        try:
            from tkinter import font as tkfont

            f = tkfont.Font(family=FONT, size=17, weight="bold")
            width = self.gift_canvas.winfo_width() or 360
            budget = max(120, int(width * 0.46))
            if f.measure(txt) > budget:
                base = name
                while base and f.measure(f"【{base}…】") > budget:
                    base = base[:-1]
                txt = f"【{base}…】" if base else f"【…】"
        except Exception:
            pass
        return txt

    def _refresh_gift_foot(self):
        """脚注同时给出总钻数，避免「总额比面板里单个礼物还小」的观感矛盾。"""
        count = len(self.gift_order)
        total = getattr(self, "_gift_total_diamonds", 0)
        if total:
            self.gift_foot_var.set(
                f"仅显示整组累计 ≥{fmt_int(self.gift_min)} 钻 · 共 {count} 条 · 合计 {fmt_int(total)} 钻"
            )
        else:
            self.gift_foot_var.set(
                f"仅显示整组累计 ≥{fmt_int(self.gift_min)} 钻 · 共 {count} 条"
            )

    def _refresh_header_stats(self, event: dict | None = None) -> None:
        if event is not None:
            if event.get("online") is not None:
                self._online_text = fmt_int(event["online"])
            gain = event.get("follower_gain")
            if gain is not None:
                self._gain_text = f"+{fmt_int(gain)}"
            payers = event.get("payers")
            if payers is not None:
                self._payers_text = fmt_int(payers)
        self.header_stats_var.set(
            f"在线人数 {getattr(self, '_online_text', '--')}        "
            f"本场涨粉 {getattr(self, '_gain_text', '--')}        "
            f"付费人数 {getattr(self, '_payers_text', '--')}"
        )

    def _on_contrib_wheel(self, event):
        step = -1 if event.delta > 0 else 1
        max_off = max(0, len(self._contrib) - CONTRIB_VISIBLE)
        self._contrib_offset = max(0, min(self._contrib_offset + step, max_off))
        self._refresh_contrib()
        return "break"

    def _refresh_contrib(self) -> None:
        items = sorted(
            self._contrib.values(), key=lambda r: -int(r.get("diamonds") or 0)
        )[:CONTRIB_KEEP]
        self.contrib_badge.configure(text=fmt_int(len(items)))
        self._contrib_offset = min(
            self._contrib_offset, max(0, len(items) - CONTRIB_VISIBLE)
        )
        start = self._contrib_offset
        window = items[start : start + CONTRIB_VISIBLE]
        for idx, (row, rank, name, value) in enumerate(self.contrib_cells):
            if idx < len(window):
                item = window[idx]
                rank.configure(text=str(start + idx + 1))
                nick = str(item.get("nick") or "")
                name.configure(text=nick if len(nick) <= 12 else nick[:11] + "…")
                value.configure(text=f"{fmt_int(int(item.get('diamonds') or 0))} 钻")
                row.pack(fill="x", pady=1)
            else:
                row.pack_forget()
        for cell in self.contrib_cells:
            for widget in cell:
                widget.bind("<MouseWheel>", self._on_contrib_wheel)

    def _add_contrib(self, event: dict) -> None:
        uid = str(event.get("user_id") or "")
        if not uid:
            return
        try:
            added = int(event.get("added") or 0)
        except (TypeError, ValueError):
            added = 0
        if added <= 0:
            return
        item = self._contrib.setdefault(uid, {"nick": "", "diamonds": 0})
        item["nick"] = str(event.get("nickname") or item["nick"] or "?")
        item["diamonds"] = int(item.get("diamonds") or 0) + added
        self._refresh_contrib()

    def _apply_gift_totals(self, event: dict) -> None:
        """礼物事件带上本场累计值，避免「总钻数小于单条礼物」。"""
        total = event.get("session_diamonds")
        if total is not None:
            shown = max(int(total), self._gift_total_diamonds)
            self._gift_total_diamonds = shown
            self.summary_vars["gift"].set(fmt_int(shown))
            self._refresh_gift_foot()
        row_total = event.get("diamonds")
        if row_total:
            try:
                row_total = int(row_total)
            except (TypeError, ValueError):
                row_total = 0
            if row_total > self._gift_total_diamonds:
                self._gift_total_diamonds = row_total
                self.summary_vars["gift"].set(fmt_int(row_total))
                self._refresh_gift_foot()

    def _gift_visible(self, event: dict) -> bool:
        try:
            diamonds = int(event.get("diamonds") or 0)
        except (TypeError, ValueError):
            diamonds = 0
        return diamonds >= self.gift_min

    def _remember_gift(self, event: dict, key: str) -> None:
        """保留每一条礼物，改门槛时可以重新筛选。"""
        if not key:
            return
        for idx, seen in enumerate(self._gift_seen):
            if seen.get("_key") == key:
                merged = dict(event)
                merged["_key"] = key
                self._gift_seen[idx] = merged
                return
        item = dict(event)
        item["_key"] = key
        self._gift_seen.append(item)
        if len(self._gift_seen) > 500:
            del self._gift_seen[: len(self._gift_seen) - 500]

    def _apply_gift_min(self) -> None:
        raw = (self.gift_min_var.get() or "").strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        value = int(digits) if digits else GIFT_MIN_DEFAULT
        value = max(0, min(value, 1_000_000))
        self.gift_min_var.set(str(value))
        if value == self.gift_min:
            return
        self.gift_min = value
        self._rebuild_gift_list()

    def _rebuild_gift_list(self) -> None:
        for info in self.gift_rows.values():
            info["frame"].destroy()
        self.gift_rows.clear()
        self.gift_order.clear()
        self.gift_count = 0
        self.gift_canvas.yview_moveto(0.0)
        self._hide_gift_placeholder()
        for event in self._gift_seen:
            if self._gift_visible(event):
                self._add_gift_row(event)
                self.gift_count += 1
        if not self.gift_order:
            self._show_gift_placeholder()
        self.gift_badge.configure(text=str(len(self.gift_order)))
        self._refresh_gift_foot()
        self._fit_gift_rows()
        self._sync_gift_scroll()
        try:
            self.gift_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _add_gift_row(self, event: dict) -> dict:
        try:
            pinned = self.gift_canvas.yview()[1] > 0.999
        except Exception:
            pinned = True
        self._hide_gift_placeholder()
        info = self._gift_row(event)
        self._update_gift_row(info, event)
        key = info["key"]
        self.gift_rows[key] = info
        self.gift_order.append(key)
        while len(self.gift_order) > GIFT_LIMIT:
            old = self.gift_order.pop(0)
            old_info = self.gift_rows.pop(old, None)
            if old_info:
                old_info["frame"].destroy()
        self.gift_badge.configure(text=str(len(self.gift_order)))
        self._refresh_gift_foot()
        self.gift_canvas.update_idletasks()
        self._fit_gift_rows()
        self._sync_gift_scroll()
        if pinned:
            self.gift_canvas.yview_moveto(1.0)
        return info

    def _bump_gift(self, info: dict):
        info["frame"].pack_forget()
        info["frame"].pack(fill="x", pady=0)
        try:
            self.gift_order.remove(info["key"])
        except ValueError:
            pass
        self.gift_order.append(info["key"])
        try:
            pinned = self.gift_canvas.yview()[1] > 0.999
        except Exception:
            pinned = True
        self.gift_canvas.update_idletasks()
        self._fit_gift_rows()
        self._sync_gift_scroll()
        if pinned:
            self.gift_canvas.yview_moveto(1.0)

    # ------------------------------------------------------------------
    # 状态栏
    # ------------------------------------------------------------------
    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=20, pady=(0, 8))
        self.hint_var = tk.StringVar(value="等待连接")
        tk.Label(
            bar, textvariable=self.hint_var, bg=BG, fg="#6f7b90", font=(FONT, 8), anchor="w"
        ).pack(side="left")
        self.csv_var = tk.StringVar(value="")
        tk.Label(
            bar,
            textvariable=self.csv_var,
            bg=BG,
            fg="#596579",
            font=(FONT, 8),
            anchor="e",
        ).pack(side="right")

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------
    def connect(self):
        raw = self.room_var.get().strip()
        if not raw:
            messagebox.showwarning("直播间", "请先粘贴抖音直播间链接或房间号。")
            return
        try:
            live_id = parse_live_id(raw)
        except SystemExit as exc:
            messagebox.showerror("直播间", str(exc))
            return

        self.disconnect()
        self._clear_feed()
        self.events = queue.Queue()
        cookie = self.cookie_var.get().strip()
        self.client = DanmakuClient(live_id, self.events, cookie=cookie)
        self.thread = threading.Thread(target=self.client.run, daemon=True)
        self.thread.start()
        self.connect_btn.configure(
            state="disabled", bg="#3b3218", fg="#e8c36a", text="连接中…"
        )
        self.disconnect_btn.configure(state="normal", fg=FG)
        self.hint_var.set(f"直播间 {live_id} · 连接中…")
        self._refresh_cookie_status()
        self.csv_var.set("")
        self._set_status("连接中…", GOLD)

    def disconnect(self):
        client = self.client
        if (
            client is not None
            and self.total_events > 0
            and self.total_events != self._last_auto_export_events
        ):
            if self.export_site(open_browser=False, quiet=True):
                self._last_auto_export_events = self.total_events
        if client is not None:
            client.stop()
            self.client = None
        self.connect_btn.configure(
            state="normal", bg=ACCENT, fg="#ffffff", text="连接"
        )
        self.disconnect_btn.configure(state="disabled", fg=MUTED)
        if client is not None:
            self._set_status("已断开", MUTED)

    def on_close(self):
        if self.login_cancel is not None:
            self.login_cancel.set()
        try:
            if self.client is not None and self.total_events > 0:
                self.export_site(open_browser=False, quiet=True)
        except Exception:
            pass
        if self.client is not None:
            self.client.stop()
        self._stop_site_server()
        self.root.destroy()

    def _stop_site_server(self):
        server = self.site_server
        self.site_server = None
        self._site_server_dir = None
        if server is not None:
            try:
                server.stop()
            except Exception:
                pass

    def _auto_export_loop(self):
        """定期把当天日志刷进静态站点；跨天时会自动生成新的日期页。"""
        try:
            if (
                self.client is not None
                and self.total_events > 0
                and self.total_events != self._last_auto_export_events
            ):
                if self.export_site(open_browser=False, quiet=True):
                    self._last_auto_export_events = self.total_events
        except Exception:
            pass
        finally:
            self._auto_export_job = self.root.after(120_000, self._auto_export_loop)

    def _handle_stream_ended(self):
        """下播：清空窗口数据并等待下一场；归档导出改为手动（点「导出」）。"""
        if self._auto_exported_on_end:
            return
        self._auto_exported_on_end = True
        self._clear_feed()
        if self.chat_placeholder is not None:
            self.chat_placeholder.configure(text="直播已结束，等待下一场…")
            self.chat_placeholder.place(relx=0.5, rely=0.5, anchor="center")
        self._set_status("直播已结束 · 需要归档请点「导出」", GOLD)
        self.hint_var.set("直播已结束 · 点「导出」可归档并打开页面")

    def _export_after_stream_end(self, had_events: int | None = None):
        """主播下播：把本场日志导出成归档站点，并直接打开浏览器页面。"""
        try:
            events = self.total_events if had_events is None else had_events
            if events <= 0:
                return
            if self.export_site(open_browser=True, quiet=True):
                self.hint_var.set("直播已结束 · 已自动导出归档并打开页面")
                self._set_status("直播已结束 · 归档已打开", MUTED)
                # 下一场会有新数据，让定期导出重新生效
                self._last_auto_export_events = -1
        except Exception:
            pass

    def export_site(self, open_browser: bool = True, quiet: bool = False) -> bool:
        try:
            import site_builder

            live_id = getattr(self.client, "live_id", None) if self.client else None
            if not live_id:
                try:
                    live_id = parse_live_id(self.room_var.get())
                except Exception:
                    live_id = None
            out = app_dir() / (f"site_{live_id}" if live_id else "site")
            logs = app_dir() / "logs"
            data = site_builder.load_messages_from_logs(logs, live_id=live_id)
            if not data:
                if not quiet:
                    messagebox.showinfo("导出网站", "还没有可导出的日志")
                return False
            meta = site_builder.load_room_meta(logs, live_id)
            anchor = str(meta.get("anchor_name") or "").strip()
            if anchor and live_id:
                title = f"{anchor} · 抖音弹幕归档 · {live_id}"
            else:
                title = f"抖音弹幕归档 · {live_id}" if live_id else "抖音弹幕归档"
            site_builder.build_site(
                data, out, title=title, meta=meta, live_id=live_id
            )

            def on_delete(day: str) -> bool:
                if not live_id:
                    return False
                site_builder.mark_day_deleted(logs, live_id, day)
                remaining = site_builder.load_messages_from_logs(
                    logs, live_id=live_id
                )
                site_builder.build_site(
                    remaining,
                    out,
                    title=title,
                    meta=meta,
                    live_id=live_id,
                )
                self._last_auto_export_events = -1
                return True

            if open_browser:
                import webbrowser

                if self.site_server is None or self._site_server_dir != out:
                    self._stop_site_server()
                    try:
                        from site_server import start_site_server

                        self.site_server = start_site_server(out, on_delete)
                        self._site_server_dir = out
                    except Exception:
                        self.site_server = None
                        self._site_server_dir = None
                if self.site_server is not None:
                    webbrowser.open(
                        f"http://127.0.0.1:{self.site_server.port}/index.html"
                    )
                else:
                    webbrowser.open((out / "index.html").as_uri())
            if not quiet:
                messagebox.showinfo("导出网站", f"已导出 {len(data)} 条消息到：\n{out}")
            return True
        except Exception as exc:
            if not quiet:
                messagebox.showerror("导出网站失败", str(exc))
            return False

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def drain_events(self):
        processed = 0
        while processed < 80:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            processed += 1
            try:
                if event.get("kind") in ("login_status", "login_done", "login_error"):
                    self._handle_login_event(event)
                else:
                    self._handle(event)
            except Exception:
                pass
        self.root.after(100, self.drain_events)

    def _handle(self, event: dict):
        kind = event.get("kind")
        self.total_events += 1
        if kind == "chat":
            self._hide_chat_placeholder()
            level = int(event.get("fans_club_level") or 0)
            pay = int(event.get("pay_level") or 0)
            is_admin = any("mark_admin" in n for n in self._chat_badge_names(event))
            if is_admin:
                tags = ("admin",)
            elif pay > 40 or level >= 18:
                tags = ("super",)
            elif pay > 35 or level >= 15:
                tags = ("hot",)
            else:
                tags = ("odd" if self.chat_count % 2 else "even",)
            self._append(
                (event.get("nickname", ""), event.get("content", "")),
                tags,
                event,
            )
        elif kind == "entry":
            self._hide_chat_placeholder()
            self._append(
                (event.get("nickname", ""), "入场  进入直播间"),
                ("entry",),
                event,
            )
        elif kind == "gift":
            self._add_contrib(event)
            key = str(event.get("group_key") or event.get("msg_id") or "")
            self._remember_gift(event, key)
            info = self.gift_rows.get(key) if key else None
            if info:
                self._update_gift_row(info, event)
                self._bump_gift(info)
            elif self._gift_visible(event):
                self._add_gift_row(event)
                self.gift_count += 1
            self._apply_gift_totals(event)
        elif kind == "summary":
            self._refresh_header_stats(event)
            online = event.get("online")
            if online:
                self.summary_vars["online"].set(fmt_int(online))
                self._online_text = fmt_int(online)
                self._refresh_online_top()
            gain = event.get("follower_gain")
            if gain is not None:
                self.summary_vars["gain"].set(f"+{fmt_int(gain)}")
            payers = event.get("payers")
            if payers is not None:
                self.summary_vars["payers"].set(fmt_int(payers))
                fan_payers = event.get("fan_payers") or 0
                self.payer_detail.set(f"粉丝团 {fan_payers} 人" if fan_payers else "")
            diamonds = event.get("gift_diamonds")
            if diamonds is not None:
                shown = max(int(diamonds), self._gift_total_diamonds)
                self.summary_vars["gift"].set(fmt_int(shown))
                if shown != self._gift_total_diamonds:
                    self._gift_total_diamonds = shown
                self._refresh_gift_foot()
            pv = event.get("pv")
            if pv:
                self._pv_text = str(pv)
                self._refresh_online_top()
            msg_count = event.get("msg_count")
            if msg_count:
                self.hint_var.set(f"已接收 {fmt_int(msg_count)} 条消息")
        elif kind == "status":
            state = event.get("state")
            text = event.get("text", "")
            color = {
                "connected": GREEN,
                "connecting": GOLD,
                "reconnecting": GOLD,
                "waiting": GOLD,
                "disconnected": RED,
                "error": RED,
                "ended": RED,
                "stopped": MUTED,
            }.get(state, MUTED)
            self._set_status(text, color)
            if state == "connected":
                self.connect_btn.configure(
                    state="disabled", bg="#123128", fg=GREEN, text="已连接"
                )
                self.disconnect_btn.configure(state="normal", fg=FG)
            elif state in ("connecting", "reconnecting"):
                self.connect_btn.configure(
                    state="disabled", bg="#3b3218", fg="#e8c36a", text="连接中…"
                )
            elif state == "waiting":
                self.connect_btn.configure(
                    state="disabled", bg="#3b3218", fg="#e8c36a", text="等待开播…"
                )
                self.disconnect_btn.configure(state="normal", fg=FG)
            elif state in ("stopped", "ended", "disconnected", "error"):
                self.connect_btn.configure(
                    state="normal", bg=ACCENT, fg="#ffffff", text="连接"
                )
                self.disconnect_btn.configure(state="disabled", fg=MUTED)
            if state == "connected" and self.client and self.client.csv_path:
                self.csv_var.set(f"CSV: {self.client.csv_path}")
            if state == "connected":
                self._auto_exported_on_end = False
            elif state == "ended":
                self._handle_stream_ended()
        elif kind == "error":
            self.hint_var.set(event.get("text", ""))

    def _append(self, values, tags, event=None):
        self._add_chat_row(values, tags, event)

    def _hide_chat_placeholder(self):
        if self.chat_placeholder is not None:
            self.chat_placeholder.place_forget()
            self.chat_placeholder = None

    def _copy_chat(self, event=None, text=None):
        if text is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(str(text))
        self.hint_var.set("已复制完整弹幕内容")

    def _clear_feed(self):
        for info in self.chat_rows:
            info["frame"].destroy()
        self.chat_rows.clear()
        for info in self.gift_rows.values():
            info["frame"].destroy()
        self.gift_rows.clear()
        self.gift_order.clear()
        self._gift_seen.clear()
        self._contrib.clear()
        self._contrib_offset = 0
        self.gift_canvas.yview_moveto(0.0)
        self._sync_gift_scroll()
        self._hide_gift_placeholder()
        self._show_gift_placeholder()
        self.chat_count = self.gift_count = 0
        self.total_events = 0
        self.chat_badge.configure(text="0")
        self.gift_badge.configure(text="0")
        if self.chat_placeholder is None:
            self.chat_placeholder = tk.Label(
                self.chat_canvas.master,
                text="等待弹幕…",
                bg=CARD,
                fg="#647086",
                font=(FONT, 15),
            )
            self.chat_placeholder.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self.chat_placeholder.place(relx=0.5, rely=0.5, anchor="center")
        for var in self.summary_vars.values():
            var.set("--")
        self.payer_detail.set("")
        self._online_text = "--"
        self._gain_text = "--"
        self._payers_text = "--"
        self._pv_text = ""
        self.online_top_var.set("在线 --")
        self._refresh_header_stats()
        self._refresh_contrib()

    def _refresh_online_top(self):
        text = f"在线 {self._online_text}"
        if self._pv_text:
            text += f" · 累计 {self._pv_text}"
        self.online_top_var.set(text)

    def _set_status(self, text: str, color: str):
        self.status_var.set(text)
        self.status_dot.itemconfigure(self._dot, fill=color)


def _redirect_console_output():
    """Keep GUI runs silent; diagnostics go to logs instead of a console."""
    try:
        log_dir = app_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = open(log_dir / "stdout.log", "a", encoding="utf-8", buffering=1)
        stderr_log = open(log_dir / "stderr.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = stdout_log
        sys.stderr = stderr_log
    except Exception:
        pass


def main():
    _redirect_console_output()
    if getattr(sys, "frozen", False):
        try:
            import ctypes

            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
        except Exception:
            pass
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    room = None
    for index, arg in enumerate(sys.argv):
        if arg == "--room" and index + 1 < len(sys.argv):
            room = sys.argv[index + 1]
        elif arg.startswith("--room="):
            room = arg.split("=", 1)[1]

    if "--export-site" in sys.argv:
        import site_builder

        live_id = parse_live_id(room) if room else ""
        if not live_id:
            print("EXPORT_SITE_ERROR missing --room")
            return
        log_dir = app_dir() / "logs"
        rows = site_builder.load_messages_from_logs(log_dir, live_id)
        meta = site_builder.load_room_meta(log_dir, live_id)
        anchor = meta.get("anchor_name") or live_id
        out = app_dir() / f"site_{live_id}"
        site_builder.build_site(
            rows,
            out,
            title=f"{anchor} · 抖音弹幕归档 · {live_id}",
            meta=meta,
            live_id=live_id,
        )
        print(f"EXPORT_SITE_OK {len(rows)} {out}")
        return

    if _focus_existing_window():
        return
    if not _acquire_single_instance():
        _focus_existing_window()
        return

    root = tk.Tk()
    app = AuroraApp(root)
    if room:
        app.room_var.set(room)
    if "--auto-connect" in sys.argv:
        root.after(900, app.connect)
    root._app = app
    root.mainloop()


if __name__ == "__main__":
    main()
