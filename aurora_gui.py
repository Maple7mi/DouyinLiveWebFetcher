#!/usr/bin/env python
# coding: utf-8
"""弹幕助手 · Aurora Dark

单一弹幕流：聊天 + 粉丝团 Lv.15+ 入场混排；
底部可滚动礼物栏，默认显示最近 5 条 >90 钻礼物，格式为：
    消费Lv.26 粉丝Lv.16 小番茄 送出了【跑车】x1，价值1,200钻
界面不显示时间戳，但 CSV / 导出网站仍保留完整时间。
"""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from collect import parse_live_id
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
APP_NAME = "弹幕助手"
WINDOW_TITLE = f"{APP_NAME} · Aurora Dark"
GIFT_LIMIT = 50
_SINGLE_MUTEX = None


def _focus_existing_window() -> bool:
    """Windows 下若已有同名窗口，直接聚焦它，避免重复开窗。"""
    if sys.platform != "win32":
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
        _SINGLE_MUTEX = kernel32.CreateMutexW(None, False, "Local\\DanmakuAssistantAurora")
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
        self.chat_rows: list[dict] = []
        self._pv_text = ""
        self._online_text = "--"
        self._last_auto_export_events = -1
        self._auto_export_job = None
        self.site_server = None
        self._site_server_dir: Path | None = None

        self._setup_style()
        self._build_bottombar()
        self._build_shell()

        self.root.after(60, self._dark_titlebar)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.drain_events)
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
        self.shell.grid_columnconfigure(0, weight=7)
        self.shell.grid_columnconfigure(1, weight=3)

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
        tk.Label(
            head, text="聊天 / 入场 / 礼物", bg=CARD, fg=MUTED, font=(FONT, 12)
        ).pack(side="left", padx=(9, 0), pady=(5, 0))
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
            text="礼物 90+",
            bg=CARD, fg=MAGENTA, font=(FONT, 13),
        ).pack(side="left", padx=(16, 0))

        table_head = tk.Frame(panel, bg="#151a25")
        table_head.pack(fill="x", padx=12)
        for text, width in (("消费等级", 6), ("粉丝团", 6), ("昵称", 10)):
            tk.Label(
                table_head, text=text, width=width, anchor="w", bg="#151a25",
                fg="#8d99ae", font=(FONT, 13, "bold"),
            ).pack(side="left", padx=(10, 0), pady=7)
        tk.Label(
            table_head, text="弹幕内容", anchor="w", bg="#151a25",
            fg="#8d99ae", font=(FONT, 13, "bold"),
        ).pack(side="left", padx=(10, 0), pady=7)

        body = tk.Frame(panel, bg=CARD)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 0))
        self.chat_canvas = tk.Canvas(
            body, bg=CARD, highlightthickness=0, borderwidth=0, yscrollincrement=30
        )
        self.chat_scroll = tk.Scrollbar(
            body, orient="vertical", command=self.chat_canvas.yview,
            bg="#171d29", troughcolor=CARD, activebackground="#2a3548",
            highlightthickness=0, bd=0, relief="flat", width=11,
        )
        self.chat_canvas.configure(yscrollcommand=self.chat_scroll.set)
        self.chat_scroll.pack(side="right", fill="y")
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

        # 2x2 指标
        metrics = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        metrics.pack(fill="x", pady=(0, 8))
        specs = [
            ("online", "当前在线", "人", ACCENT),
            ("gain", "本场涨粉", "人", BLUE),
            ("payers", "付费人数", "人", RED),
            ("gift", "礼物总额", "钻", GOLD),
        ]
        self.summary_vars: dict[str, tk.StringVar] = {}
        for idx, (key, label, unit, color) in enumerate(specs):
            r, c = divmod(idx, 2)
            metrics.grid_columnconfigure(c, weight=1)
            cell = tk.Frame(metrics, bg=CARD)
            cell.grid(row=r, column=c, sticky="nsew", padx=(12 if c == 0 else 7, 12 if c == 1 else 7), pady=9)
            tk.Label(cell, text=label, bg=CARD, fg=MUTED, font=(FONT, 11)).pack(anchor="w")
            line = tk.Frame(cell, bg=CARD)
            line.pack(anchor="w")
            var = tk.StringVar(value="--")
            self.summary_vars[key] = var
            tk.Label(
                line, textvariable=var, bg=CARD,
                fg=GOLD if key == "gift" else FG,
                font=(FONT, 24, "bold"),
            ).pack(side="left")
            tk.Label(line, text=unit, bg=CARD, fg=MUTED, font=(FONT, 10)).pack(
                side="left", padx=(3, 0), pady=(0, 3)
            )
            if key == "payers":
                self.payer_detail = tk.StringVar(value="")
                tk.Label(
                    cell, textvariable=self.payer_detail, bg=CARD, fg=MUTED, font=(FONT, 9)
                ).pack(anchor="w")

        # 礼物
        gift_card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        gift_card.pack(fill="both", expand=True)
        gift_head = tk.Frame(gift_card, bg=CARD)
        gift_head.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(
            gift_head, text="GIFT FEED", bg=CARD, fg=MAGENTA, font=(FONT, 9, "bold")
        ).pack(side="left")
        tk.Label(
            gift_head, text="礼物 · 最近 5 条", bg=CARD, fg=FG, font=(FONT, 14, "bold")
        ).pack(side="left", padx=(6, 0))
        self.gift_badge = tk.Label(
            gift_head, text="0", bg="#2a1020", fg="#ffb4d6", font=(FONT, 9, "bold"),
            padx=7, pady=1,
        )
        self.gift_badge.pack(side="right")

        gift_body = tk.Frame(gift_card, bg=CARD)
        gift_body.pack(fill="x", padx=8, pady=(0, 8))
        self.gift_canvas = tk.Canvas(
            gift_body, bg=CARD, height=280, highlightthickness=0, borderwidth=0, yscrollincrement=30
        )
        self.gift_scroll = tk.Scrollbar(
            gift_body, orient="vertical", command=self.gift_canvas.yview, width=9,
            bg="#241522", troughcolor="#0c1018", activebackground=MAGENTA,
            relief="flat", borderwidth=0,
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

        gift_foot = tk.Frame(gift_card, bg=CARD)
        gift_foot.pack(side="bottom", fill="x", padx=12, pady=(0, 8))
        tk.Label(
            gift_foot,
            text="仅显示整组累计 >90 钻 · 滚轮回看",
            bg=CARD,
            fg="#657187",
            font=(FONT, 9),
        ).pack(side="left")

    def _build_bottombar(self):
        bar = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        bar.pack(side="bottom", fill="x", padx=14, pady=(0, 8))
        row = tk.Frame(bar, bg=CARD)
        row.pack(fill="x", padx=10, pady=(5, 2))
        tk.Label(row, text="Cookie", bg=CARD, fg=MUTED, font=(FONT, 9, "bold")).pack(side="left")
        self.cookie_var = tk.StringVar()
        wrap = tk.Frame(row, bg=BORDER)
        wrap.pack(side="left", fill="x", expand=True, padx=(7, 7))
        self.cookie_entry = tk.Entry(
            wrap, textvariable=self.cookie_var, bg=FIELD, fg=FG, insertbackground=FG,
            relief="flat", font=(FONT, 9), show="•",
        )
        self.cookie_entry.pack(fill="x", ipady=3, padx=1, pady=1)
        for text, command in (
            ("读取", self._load_cookie_file),
            ("保存", self._save_cookie_file),
            ("清除", self._clear_cookie),
        ):
            tk.Button(
                row, text=text, command=command, bg="#1a2030", fg="#b8c3d6",
                activebackground="#222a3c", activeforeground=FG, relief="flat", bd=0,
                font=(FONT, 9), padx=8, pady=3, cursor="hand2",
            ).pack(side="left", padx=(3, 0))
        self.cookie_hint_var = tk.StringVar(value="未填 Cookie：只能收弹幕")
        tk.Label(
            row, textvariable=self.cookie_hint_var, bg=CARD, fg="#778399", font=(FONT, 9)
        ).pack(side="left", padx=(8, 0))

        bottom = tk.Frame(bar, bg=CARD)
        bottom.pack(fill="x", padx=10, pady=(0, 5))
        self.hint_var = tk.StringVar(value="等待连接")
        tk.Label(
            bottom, textvariable=self.hint_var, bg=CARD, fg="#778399", font=(FONT, 9)
        ).pack(side="left")
        self.csv_var = tk.StringVar(value="")
        tk.Label(
            bottom, textvariable=self.csv_var, bg=CARD, fg="#596579", font=(FONT, 8)
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
        wrap = max(260, self.chat_canvas.winfo_width() - 365)
        for row in self.chat_rows:
            try:
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
        if "super" in tags:
            return "#2e1218", "#ffd7e5", "super"
        if "hot" in tags:
            return "#2a2312", "#ffe5a6", "hot"
        if "entry" in tags:
            return "#062d36", "#bdf8ff", "entry"
        return (CARD if self.chat_count % 2 == 0 else "#0d1119"), "#dfe5f4", "normal"

    def _add_chat_row(self, values, tags):
        bg, fg, kind = self._chat_bg(tags)
        pay, fan, nick, content = values
        frame = tk.Frame(self.chat_list, bg=bg)
        frame.pack(fill="x")
        frame.grid_columnconfigure(3, weight=1)

        p = tk.Label(frame, text=pay, bg=bg, fg=fg, font=(FONT, 16), width=6, anchor="w")
        p.grid(row=0, column=0, sticky="nw", padx=(10, 0), pady=6)
        f = tk.Label(frame, text=fan, bg=bg, fg=fg, font=(FONT, 16), width=6, anchor="w")
        f.grid(row=0, column=1, sticky="nw", padx=(4, 0), pady=6)
        n = tk.Label(frame, text=nick, bg=bg, fg=fg, font=(FONT, 18, "bold"), width=10, anchor="w")
        n.grid(row=0, column=2, sticky="nw", padx=(4, 0), pady=6)
        c = tk.Label(
            frame, text=content, bg=bg, fg=fg, font=(FONT, 18),
            anchor="w", justify="left",
            wraplength=max(260, self.chat_canvas.winfo_width() - 365),
        )
        c.grid(row=0, column=3, sticky="nsew", padx=(6, 10), pady=6)

        info = {"frame": frame, "content": c, "text": content}
        self.chat_rows.append(info)
        for widget in (frame, p, f, n, c):
            widget.bind("<Double-1>", lambda _e, t=content: self._copy_chat(text=t))
            widget.bind("<MouseWheel>", self._on_chat_wheel, add="+")

        while len(self.chat_rows) > 500:
            old = self.chat_rows.pop(0)
            old["frame"].destroy()

        self.chat_count += 1
        self.chat_badge.configure(text=fmt_int(self.chat_count))
        self._hide_chat_placeholder()
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
        self.cookie_hint_var.set("已读取 cookie.txt（仅本机）" if cookie else "cookie.txt 是空的")

    def _clear_cookie(self):
        self.cookie_var.set("")
        self.cookie_hint_var.set("未填 Cookie：只能收弹幕")

    def _save_cookie_file(self):
        cookie = self.cookie_var.get().strip()
        if not cookie:
            messagebox.showinfo("Cookie", "Cookie 输入框是空的，没有保存。")
            return
        try:
            (app_dir() / "cookie.txt").write_text(cookie, encoding="utf-8")
            self.cookie_hint_var.set("已保存到 cookie.txt（明文，仅本机）")
        except Exception as exc:
            messagebox.showerror("Cookie", f"保存失败：{exc}")

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
            text="入场 ≥15 青色高亮 · 礼物 >90 钻 洋红最高级高亮 · 弹幕 35+/15+ 金色、40+/18+ 红色",
            bg=CARD,
            fg="#778399",
            font=(FONT, 8),
        ).pack(side="left")

        tree_wrap = tk.Frame(panel, bg=CARD)
        tree_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 0))
        self.chat_tree = ttk.Treeview(
            tree_wrap,
            columns=("pay", "level", "nick", "content"),
            show="headings",
            style="Aurora.Treeview",
            selectmode="browse",
        )
        for key, label, width, anchor, stretch in (
            ("pay", "消费等级", 88, "center", False),
            ("level", "粉丝团", 78, "center", False),
            ("nick", "昵称", 210, "w", False),
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
            text="仅显示整组累计 >90 钻 · 滚轮回看",
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
        self._sync_gift_scroll()

    def _sync_gift_scroll(self, event=None):
        try:
            self.gift_canvas.configure(scrollregion=self.gift_canvas.bbox("all"))
        except Exception:
            pass

    def _on_gift_wheel(self, event):
        step = -3 if event.delta > 0 else 3
        self.gift_canvas.yview_scroll(step, "units")
        return "break"

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
            height=48,
            highlightbackground="#4a1d35",
            highlightthickness=1,
        )
        row.pack(fill="x", pady=0)
        row.pack_propagate(False)
        row.grid_columnconfigure(0, weight=1)
        left = tk.Frame(row, bg=bg)
        left.grid(row=0, column=0, sticky="ew", padx=(8, 0), pady=7)

        nick = tk.Label(
            left,
            text="",
            bg=bg,
            fg="#f2f5ff",
            font=(FONT, 18, "bold"),
            anchor="w",
            width=7,
        )
        nick.pack(side="left")
        pre = tk.Label(left, text="送出了", bg=bg, fg="#9aa6ba", font=(FONT, 13))
        pre.pack(side="left", padx=(4, 0))
        gift = tk.Label(
            left,
            text="",
            bg=bg,
            fg="#ff9cc5",
            font=(FONT, 18, "bold"),
            anchor="w",
            width=10,
        )
        gift.pack(side="left", padx=(3, 0))
        mult = tk.Label(
            row,
            text="",
            bg=bg,
            fg=GOLD,
            font=(FONT, 18, "bold"),
            anchor="e",
            padx=0,
        )
        mult.grid(row=0, column=1, sticky="e", padx=(3, 8), pady=7)

        self._bind_gift_wheel(row)
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
        info["gift"].configure(text=f"【{event.get('gift_name', '')}】")
        info["mult"].configure(
            text=f"×{count}  价值{fmt_int(event.get('diamonds', 0))}钻"
        )

    def _add_gift_row(self, event: dict) -> dict:
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
        self.gift_badge.configure(text=str(min(self.gift_count + 1, GIFT_LIMIT)))
        self.gift_canvas.update_idletasks()
        self._sync_gift_scroll()
        self.gift_canvas.yview_moveto(1.0)
        return info

    def _bump_gift(self, info: dict):
        info["frame"].pack_forget()
        info["frame"].pack(fill="x", pady=2)
        try:
            self.gift_order.remove(info["key"])
        except ValueError:
            pass
        self.gift_order.append(info["key"])
        self.gift_canvas.update_idletasks()
        self._sync_gift_scroll()
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
        if cookie:
            self.cookie_hint_var.set("已填 Cookie：尝试接收礼物消息")
        else:
            self.cookie_hint_var.set("未填 Cookie：只能收弹幕")
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
        while processed < 300:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            processed += 1
            try:
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
            if pay > 40 or level >= 18:
                tags = ("super",)
            elif pay > 35 or level >= 15:
                tags = ("hot",)
            else:
                tags = ("odd" if self.chat_count % 2 else "even",)
            self._append(
                (
                    f"Lv.{pay}" if pay else "—",
                    f"Lv.{level}" if level else "—",
                    event.get("nickname", ""),
                    event.get("content", ""),
                ),
                tags,
            )
        elif kind == "entry":
            self._hide_chat_placeholder()
            level = int(event.get("fans_club_level") or 0)
            self._append(
                ("—", f"Lv.{level}" if level else "—", event.get("nickname", ""), "入场  进入直播间"),
                ("entry",),
            )
        elif kind == "gift":
            key = str(event.get("group_key") or event.get("msg_id") or "")
            info = self.gift_rows.get(key) if key else None
            if info:
                self._update_gift_row(info, event)
                self._bump_gift(info)
            else:
                self._add_gift_row(event)
                self.gift_count += 1
        elif kind == "summary":
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
                self.summary_vars["gift"].set(fmt_int(diamonds))
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
        elif kind == "error":
            self.hint_var.set(event.get("text", ""))

    def _append(self, values, tags):
        self._add_chat_row(values, tags)

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
        self._pv_text = ""
        self.online_top_var.set("在线 --")

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
