#!/usr/bin/env python
# coding: utf-8
"""抖音直播弹幕姬（Tkinter 桌面窗口）。

四个分区：
1. 弹幕：全部聊天，含消费等级、粉丝团灯牌等级、昵称、内容；
2. 入场：只看粉丝团灯牌 > 15 级的进场；
3. 礼物：只显示单条总价值 > 90 钻（9 元）的礼物；
4. 直播总结：当前在线、本场涨粉、付费人数、礼物总额、累计观看。

用法：
    .venv\\Scripts\\python.exe danmaku_gui.py
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import websocket

from collect import parse_live_id
from liveMan import DouyinLiveWebFetcher
from protobuf.douyin import (
    ChatMessage,
    ControlMessage,
    EmojiChatMessage,
    GiftMessage,
    MemberMessage,
    PushFrame,
    Response,
    RoomStatsMessage,
    RoomUserSeqMessage,
    SocialMessage,
)

# ---------------------------------------------------------------------------
# 主题
# ---------------------------------------------------------------------------
BG = "#0b0e14"
CARD = "#141922"
CARD_ALT = "#11161e"
BORDER = "#242b38"
FG = "#e8edf5"
MUTED = "#8b96a8"
ACCENT = "#ff2c55"
ACCENT_DIM = "#ff5f7d"
GREEN = "#2dd4a7"
YELLOW = "#f5c451"
BLUE = "#5aa9ff"
FONT = "Microsoft YaHei UI"


def app_dir() -> Path:
    """可写文件（cookie/logs）放在 exe 或源码目录旁边。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def ts_now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def fmt_int(value) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value or "--")


class DanmakuClient(DouyinLiveWebFetcher):
    """带重连、过滤和统计的采集客户端。"""

    def __init__(self, live_id: str, events: queue.Queue, cookie: str = ""):
        super().__init__(live_id, cookie=cookie)
        self.events = events
        self._stop = threading.Event()
        self._seen: set[tuple[str, str]] = set()
        self._gift_repeat: dict[int, int] = {}
        self._gift_group_total: dict[int, int] = {}
        self._gift_group_emitted: dict[int, int] = {}
        self._followers: int | None = None
        self._follow_base: int | None = None
        self._online = 0
        self._pv = ""
        self._payers: set[str] = set()
        self._fan_payers: set[str] = set()
        self._gift_diamonds = 0
        self._msg_count = 0
        self._last_summary = 0.0
        self._csv_file = None
        self._csv_writer = None
        self._gift_raw = None
        self.csv_path: Path | None = None

    # -- 生命周期 ---------------------------------------------------------
    def run(self):
        self._open_logs()
        try:
            while not self._stop.is_set():
                self._emit("status", state="connecting", text="连接中…")
                try:
                    self._connectWebSocket()
                except Exception as exc:
                    self._emit("status", state="error", text=f"连接失败：{exc}")
                if self._stop.is_set():
                    break
                self._emit("status", state="reconnecting", text="连接断开，5 秒后重连…")
                if self._stop.wait(5):
                    break
        finally:
            self._close_logs()
            self._emit("status", state="stopped", text="已停止")

    def stop(self):
        self._stop.set()
        ws = getattr(self, "ws", None)
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _open_logs(self):
        logs = app_dir() / "logs"
        logs.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.csv_path = logs / f"gui_{self.live_id}_{stamp}.csv"
        self._csv_file = self.csv_path.open("w", newline="", encoding="utf-8-sig")
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow(
            [
                "received_at",
                "kind",
                "msg_id",
                "user_id",
                "nickname",
                "fans_club_level",
                "pay_level",
                "content",
                "gift_name",
                "gift_count",
                "diamonds",
                "group_id",
                "visible",
            ]
        )
        self._csv_file.flush()
        self._gift_raw = (logs / f"gifts_raw_{self.live_id}_{stamp}.jsonl").open(
            "w", encoding="utf-8"
        )

    def _close_logs(self):
        for handle in (self._csv_file, self._gift_raw):
            try:
                if handle:
                    handle.close()
            except Exception:
                pass

    # -- 工具 -------------------------------------------------------------
    def _debug(self, text: str):
        if not os.environ.get("DANMAKU_DEBUG"):
            return
        try:
            path = app_dir() / "logs" / "debug.log"
            path.parent.mkdir(exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
        except Exception:
            pass

    def _emit(self, kind: str, **data):
        data["kind"] = kind
        data["ts"] = ts_now()
        self._debug(f"EVENT {data}")
        self.events.put(data)

    def _write_csv(self, kind: str, **data):
        if not self._csv_writer:
            return
        try:
            self._csv_writer.writerow(
                [
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    kind,
                    data.get("msg_id", ""),
                    data.get("user_id", ""),
                    data.get("nickname", ""),
                    data.get("fans_club_level", ""),
                    data.get("pay_level", ""),
                    data.get("content", ""),
                    data.get("gift_name", ""),
                    data.get("gift_count", ""),
                    data.get("diamonds", ""),
                    data.get("group_id", ""),
                    data.get("visible", ""),
                ]
            )
            self._csv_file.flush()
        except Exception:
            pass

    def _once(self, kind: str, msg_id: str) -> bool:
        if not msg_id:
            return True
        key = (kind, msg_id)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    def _followers_gain(self) -> int | None:
        if self._followers is None or self._follow_base is None:
            return None
        return max(0, self._followers - self._follow_base)

    def _emit_summary(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self._last_summary < 0.4:
            return
        self._last_summary = now
        self._emit(
            "summary",
            online=self._online,
            followers=self._followers,
            follower_gain=self._followers_gain(),
            payers=len(self._payers),
            fan_payers=len(self._fan_payers),
            gift_diamonds=self._gift_diamonds,
            pv=self._pv,
            msg_count=self._msg_count,
        )

    # -- 消息解析 ---------------------------------------------------------
    def _wsOnOpen(self, ws):
        self._emit("status", state="connected", text="已连接")
        threading.Thread(target=self._sendHeartbeat, daemon=True).start()

    def _wsOnClose(self, ws, *args):
        self._emit("status", state="disconnected", text="连接断开")

    def _wsOnError(self, ws, error):
        self._emit("status", state="error", text=f"网络错误：{error}")

    def _wsOnMessage(self, ws, message):
        try:
            package = PushFrame().parse(message)
            response = Response().parse(gzip.decompress(package.payload))
        except Exception as exc:
            self._debug("PARSE " + traceback.format_exc())
            self._emit("error", text=f"数据解包失败：{exc}")
            return

        if response.need_ack:
            try:
                ack = PushFrame(
                    log_id=package.log_id,
                    payload_type="ack",
                    payload=response.internal_ext.encode("utf-8"),
                ).SerializeToString()
                ws.send(ack, websocket.ABNF.OPCODE_BINARY)
            except Exception:
                pass

        handlers = {
            "WebcastChatMessage": self._parseChatMsg,
            "WebcastGiftMessage": self._parseGiftMsg,
            "WebcastMemberMessage": self._parseMemberMsg,
            "WebcastSocialMessage": self._parseSocialMsg,
            "WebcastRoomUserSeqMessage": self._parseRoomUserSeqMsg,
            "WebcastRoomStatsMessage": self._parseRoomStatsMsg,
            "WebcastEmojiChatMessage": self._parseEmojiChatMsg,
            "WebcastControlMessage": self._parseControlMsg,
            "WebcastFansclubMessage": self._parseFansclubMsg,
            "WebcastRoomRankMessage": self._parseRankMsg,
        }
        for msg in response.messages_list:
            handler = handlers.get(msg.method)
            if not handler:
                continue
            try:
                handler(msg.payload)
            except Exception as exc:
                self._emit("error", text=f"{msg.method} 解析失败：{exc}")

    def _parseChatMsg(self, payload):
        message = ChatMessage().parse(payload)
        user = message.user
        if not user or (not user.id and not user.nick_name):
            return
        msg_id = str(message.common.msg_id or "")
        if not self._once("chat", msg_id):
            return
        club = user.fans_club.data if user.fans_club else None
        level = int(club.level or 0) if club else 0
        pay_level = int(user.pay_grade.level or 0) if user.pay_grade else 0
        content = (message.content or "").replace("\n", " ").strip()
        if not content:
            return
        self._msg_count += 1
        data = dict(
            msg_id=msg_id,
            user_id=str(user.id),
            nickname=user.nick_name,
            fans_club_level=level,
            pay_level=pay_level,
            content=content,
        )
        self._emit("chat", **data)
        self._write_csv("chat", **data)
        self._emit_summary()

    def _parseEmojiChatMsg(self, payload):
        message = EmojiChatMessage().parse(payload)
        user = message.user
        if not user or not user.id:
            return
        msg_id = str(message.common.msg_id or "")
        if not self._once("chat", msg_id):
            return
        club = user.fans_club.data if user.fans_club else None
        level = int(club.level or 0) if club else 0
        pay_level = int(user.pay_grade.level or 0) if user.pay_grade else 0
        content = (message.default_content or "[表情]").replace("\n", " ").strip()
        data = dict(
            msg_id=msg_id,
            user_id=str(user.id),
            nickname=user.nick_name,
            fans_club_level=level,
            pay_level=pay_level,
            content=content,
        )
        self._msg_count += 1
        self._emit("chat", **data)
        self._write_csv("chat", **data)
        self._emit_summary()

    def _parseMemberMsg(self, payload):
        message = MemberMessage().parse(payload)
        user = message.user
        if not user or not user.id:
            return
        club = user.fans_club.data if user.fans_club else None
        level = int(club.level or 0) if club else 0
        pay_level = int(user.pay_grade.level or 0) if user.pay_grade else 0
        # 第二个分区只关心 >15 级粉丝团灯牌
        if level <= 15:
            return
        msg_id = str(message.common.msg_id or "")
        if not self._once("entry", msg_id):
            return
        data = dict(
            msg_id=msg_id,
            user_id=str(user.id),
            nickname=user.nick_name,
            fans_club_level=level,
            pay_level=pay_level,
            content="进入直播间",
        )
        self._emit("entry", **data)
        self._write_csv("entry", **data)
        self._emit_summary()

    def _gift_delta(self, message: GiftMessage) -> int:
        repeat = int(message.repeat_count or 0)
        combo = int(message.combo_count or 0)
        group = int(message.group_id or 0)

        # 非连击礼物：repeat_count 就是本次数量
        if repeat <= 0:
            return max(combo, 1)
        if not group:
            return repeat

        # 连击：同 group 的 repeat_count 是累计值，本次增量 = 本次 - 上次
        previous = self._gift_repeat.get(group)
        if previous is None:
            # 本组第一条：repeat_count 就是本次数量，即使 repeat_end 已置 1
            # （单次送多个礼物时会出现 repeat_count>1 且 repeat_end=1）。
            delta = repeat
        elif repeat >= previous:
            delta = repeat - previous
        else:
            delta = repeat
        self._gift_repeat[group] = repeat
        if message.repeat_end:
            self._gift_repeat.pop(group, None)
        return max(delta, 0)

    def _parseGiftMsg(self, payload):
        message = GiftMessage().parse(payload)
        user = message.user
        gift = message.gift
        if not user or not user.id or not gift or not gift.name:
            return
        msg_id = str(message.common.msg_id or "")
        repeat = int(message.repeat_count or 0)
        combo = int(message.combo_count or 0)
        group = int(message.group_id or 0)
        total_count = int(message.total_count or 0)
        unit = int(gift.diamond_count or 0)

        # 原始礼物消息留档，便于核对连击计数语义
        if self._gift_raw:
            try:
                self._gift_raw.write(
                    json.dumps(
                        {
                            "ts": datetime.now().isoformat(timespec="seconds"),
                            "msg_id": msg_id,
                            "gift_id": str(gift.id),
                            "gift_name": gift.name,
                            "unit_diamond": unit,
                            "repeat_count": repeat,
                            "combo_count": combo,
                            "group_count": int(message.group_count or 0),
                            "group_id": group,
                            "total_count": total_count,
                            "repeat_end": int(message.repeat_end or 0),
                            "user_id": str(user.id),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                self._gift_raw.flush()
            except Exception:
                pass

        if not self._once("gift", msg_id):
            return
        count = self._gift_delta(message)
        group_total = (
            self._gift_group_total.get(group, 0) + count if group else count
        )
        if group:
            self._gift_group_total[group] = group_total
        if count <= 0 and group_total <= 0:
            return

        club = user.fans_club.data if user.fans_club else None
        level = int(club.level or 0) if club else 0
        pay_level = int(user.pay_grade.level or 0) if user.pay_grade else 0
        diamonds = unit * count
        total_diamonds = unit * group_total
        uid = str(user.id)
        if count > 0:
            self._payers.add(uid)
            if level > 0:
                self._fan_payers.add(uid)
        self._gift_diamonds += diamonds
        self._msg_count += 1

        # 所有礼物都写入 CSV（含未达到 >90 钻展示门槛的），
        # 供网页版按天汇总礼物榜；界面仍然只展示 >90 钻的整组礼物。
        data = dict(
            msg_id=msg_id,
            user_id=uid,
            nickname=user.nick_name,
            fans_club_level=level,
            pay_level=pay_level,
            content=gift.name,
            gift_name=gift.name,
            gift_count=group_total,
            diamonds=total_diamonds,
            group_id=str(group) if group else "",
            visible=1 if total_diamonds > 90 else 0,
            group_key=str(group) if group else msg_id,
        )
        self._write_csv("gift", **data)

        # 连击的 repeat_count 是累计值：界面按整组累计总价判断，
        # 例如 100 个 1 钻小心心，不应被拆成 10 个 10 钻而过滤掉。
        already = self._gift_group_emitted.get(group, -1) if group else -1
        if total_diamonds > 90 and group_total != already:
            self._gift_group_emitted[group] = group_total if group else total_diamonds
            self._emit("gift", **data)
        if message.repeat_end and group:
            self._gift_group_total.pop(group, None)
            self._gift_group_emitted.pop(group, None)
        self._emit_summary()

    def _parseSocialMsg(self, payload):
        message = SocialMessage().parse(payload)
        action = int(message.action or 0)
        # action=1 关注，follow_count 是主播当前粉丝总数
        if action == 1 and message.follow_count:
            followers = int(message.follow_count)
            if self._followers is None:
                self._follow_base = followers - 1
            self._followers = followers
            self._emit_summary(force=True)

    def _parseRoomUserSeqMsg(self, payload):
        message = RoomUserSeqMessage().parse(payload)
        total = int(message.total or 0)
        if total:
            self._online = total
        if message.total_pv_for_anchor:
            self._pv = str(message.total_pv_for_anchor)
        self._emit_summary()

    def _parseRoomStatsMsg(self, payload):
        message = RoomStatsMessage().parse(payload)
        total = int(message.total or message.display_value or 0)
        if total:
            self._online = total
        self._emit_summary()

    def _parseFansclubMsg(self, payload):
        # 目前不需要单独展示粉丝团升级/入团消息
        pass

    def _parseControlMsg(self, payload):
        message = ControlMessage().parse(payload)
        if int(message.status or 0) == 3:
            self._emit("status", state="ended", text="直播已结束")
            self.stop()

    def _parseRankMsg(self, payload):
        pass


class DanmakuApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("抖音弹幕姬 · 实时弹幕 / 入场 / 礼物 / 直播总结")
        self.root.geometry("1320x860")
        self.root.minsize(1120, 720)
        self.root.configure(bg=BG)

        self.events: queue.Queue = queue.Queue()
        self.client: DanmakuClient | None = None
        self.client_thread: threading.Thread | None = None
        self.chat_count = 0
        self.entry_count = 0
        self.gift_count = 0
        self.gift_rows: dict[str, dict] = {}

        self._setup_style()
        self._build_topbar()
        self._build_cookiebar()
        self._build_statusbar()
        self._build_summary()
        self._build_body()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.drain_events)

    # -- 样式 -------------------------------------------------------------
    def _setup_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Dark.Treeview",
            background=CARD,
            fieldbackground=CARD,
            foreground=FG,
            borderwidth=0,
            rowheight=28,
            font=(FONT, 9),
        )
        style.configure(
            "Dark.Treeview.Heading",
            background="#1c222d",
            foreground=MUTED,
            borderwidth=0,
            relief="flat",
            font=(FONT, 9, "bold"),
            padding=(6, 6),
        )
        style.map(
            "Dark.Treeview",
            background=[("selected", "#2b3446")],
            foreground=[("selected", "#ffffff")],
        )
        style.map(
            "Dark.Treeview.Heading",
            background=[("active", "#232b38")],
        )
        style.configure(
            "Dark.Vertical.TScrollbar",
            background="#232b38",
            troughcolor=CARD,
            bordercolor=CARD,
            arrowcolor=MUTED,
        )

    # -- 顶部 -------------------------------------------------------------
    def _build_topbar(self):
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=18, pady=(14, 8))

        brand = tk.Frame(bar, bg=BG)
        brand.pack(side="left")
        tk.Label(
            brand,
            text="抖音弹幕姬",
            bg=BG,
            fg=FG,
            font=(FONT, 18, "bold"),
        ).pack(anchor="w")
        tk.Label(
            brand,
            text="实时弹幕 · 粉丝团灯牌 · 礼物 · 直播总结",
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
        ).pack(anchor="w", pady=(2, 0))

        right = tk.Frame(bar, bg=BG)
        right.pack(side="right")
        self.status_dot = tk.Canvas(
            right, width=10, height=10, bg=BG, highlightthickness=0
        )
        self.status_dot.pack(side="left", padx=(0, 6))
        self._dot = self.status_dot.create_oval(1, 1, 9, 9, fill=MUTED, outline="")
        self.status_var = tk.StringVar(value="未连接")
        tk.Label(
            right,
            textvariable=self.status_var,
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
        ).pack(side="left")
        self.online_top_var = tk.StringVar(value="在线 --")
        tk.Label(
            right,
            textvariable=self.online_top_var,
            bg=BG,
            fg=GREEN,
            font=(FONT, 10, "bold"),
        ).pack(side="left", padx=(18, 0))

        controls = tk.Frame(bar, bg=BG)
        controls.pack(side="right", padx=(0, 28))
        self.room_var = tk.StringVar(value="https://live.douyin.com/77217772750")
        entry = tk.Entry(
            controls,
            textvariable=self.room_var,
            bg=CARD,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=(FONT, 10),
            width=40,
        )
        entry.pack(side="left", ipady=8, padx=(0, 8))
        entry.bind("<Return>", lambda _e: self.connect())

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
            padx=18,
            pady=8,
            cursor="hand2",
        )
        self.connect_btn.pack(side="left", padx=(0, 6))
        self.disconnect_btn = tk.Button(
            controls,
            text="断开",
            command=self.disconnect,
            bg=CARD,
            fg=MUTED,
            activebackground=BORDER,
            activeforeground=FG,
            relief="flat",
            bd=0,
            font=(FONT, 10),
            padx=14,
            pady=8,
            cursor="hand2",
            state="disabled",
        )
        self.disconnect_btn.pack(side="left")
        self.export_btn = tk.Button(
            controls,
            text="导出网站",
            command=self.export_site,
            bg=CARD,
            fg=BLUE,
            activebackground=BORDER,
            activeforeground=FG,
            relief="flat",
            bd=0,
            font=(FONT, 9),
            padx=12,
            pady=9,
            cursor="hand2",
        )
        self.export_btn.pack(side="left", padx=(6, 0))

    def export_site(self):
        """把 logs 里的所有消息生成按日期分类的静态网站。"""
        try:
            import site_builder

            out = app_dir() / "site"
            data = site_builder.load_messages_from_logs(app_dir() / "logs")
            if not data:
                messagebox.showinfo("导出网站", "还没有可导出的日志")
                return
            site_builder.build_site(data, out, title="抖音弹幕归档")
            webbrowser.open((out / "index.html").as_uri())
            messagebox.showinfo("导出网站", f"已导出 {len(data)} 条消息到：\n{out}")
        except Exception as exc:
            messagebox.showerror("导出网站失败", str(exc))

    # -- Cookie -----------------------------------------------------------
    def _build_cookiebar(self):
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=18, pady=(0, 4))

        tk.Label(
            bar,
            text="Cookie",
            bg=BG,
            fg=MUTED,
            font=(FONT, 9, "bold"),
        ).pack(side="left")
        tk.Label(
            bar,
            text="登录抖音网页版后复制；填了才能收到礼物消息",
            bg=BG,
            fg="#6f7a8c",
            font=(FONT, 8),
        ).pack(side="left", padx=(6, 8))

        self.cookie_var = tk.StringVar()
        self.cookie_entry = tk.Entry(
            bar,
            textvariable=self.cookie_var,
            bg=CARD_ALT,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=(FONT, 9),
            show="•",
        )
        self.cookie_entry.pack(side="left", fill="x", expand=True, ipady=5)

        tk.Button(
            bar,
            text="读取 cookie.txt",
            command=self._load_cookie_file,
            bg=CARD,
            fg=MUTED,
            activebackground=BORDER,
            activeforeground=FG,
            relief="flat",
            bd=0,
            font=(FONT, 8),
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=(6, 4))
        tk.Button(
            bar,
            text="保存",
            command=self._save_cookie_file,
            bg=CARD,
            fg=MUTED,
            activebackground=BORDER,
            activeforeground=FG,
            relief="flat",
            bd=0,
            font=(FONT, 8),
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=(0, 4))
        tk.Button(
            bar,
            text="清除",
            command=self._clear_cookie,
            bg=CARD,
            fg=MUTED,
            activebackground=BORDER,
            activeforeground=FG,
            relief="flat",
            bd=0,
            font=(FONT, 8),
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left")

        self.cookie_hint_var = tk.StringVar(value="未填：只能收弹幕，收不到礼物")
        tk.Label(
            bar,
            textvariable=self.cookie_hint_var,
            bg=BG,
            fg="#6f7a8c",
            font=(FONT, 8),
        ).pack(side="left", padx=(8, 0))

        # 如果本机已有 cookie.txt，自动读取，省得每次粘贴。
        if (app_dir() / "cookie.txt").exists():
            self._load_cookie_file(silent=True)

    def _load_cookie_file(self, silent: bool = False):
        path = app_dir() / "cookie.txt"
        if not path.exists():
            if not silent:
                messagebox.showinfo(
                    "Cookie",
                    "没有找到 cookie.txt。\n\n"
                    "在项目目录新建 cookie.txt，把浏览器里复制的 Cookie 粘进去即可。",
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
        self.cookie_hint_var.set("未填：只能收弹幕，收不到礼物")

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

    # -- 主体 -------------------------------------------------------------
    def _build_body(self):
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=(4, 0))
        body.grid_columnconfigure(0, weight=4)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=3)
        body.grid_rowconfigure(1, weight=2)

        self.chat_card, chat_body, self.chat_badge = self._make_card(
            body, "弹幕", "全部聊天 · 消费等级 / 粉丝团 / 昵称"
        )
        self.chat_card.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 10))
        self.chat_tree = self._make_tree(
            chat_body,
            [
                ("pay", "消费等级", 82, "center", False),
                ("level", "粉丝团", 76, "center", False),
                ("nick", "昵称", 150, "w", False),
                ("content", "弹幕内容", 520, "w", True),
            ],
        )
        self.chat_tree.tag_configure("odd", background=CARD)
        self.chat_tree.tag_configure("even", background=CARD_ALT)
        self.chat_tree.tag_configure("vip", foreground="#ffd166")
        self.chat_tree.bind("<Double-1>", self._copy_chat)

        self.entry_card, entry_body, self.entry_badge = self._make_card(
            body, "入场", "粉丝团灯牌 > 15 级"
        )
        self.entry_card.grid(row=0, column=1, sticky="nsew", pady=(0, 10))
        self.entry_tree = self._make_tree(
            entry_body,
            [
                ("time", "时间", 70, "center", False),
                ("level", "粉丝团", 76, "center", False),
                ("nick", "昵称", 220, "w", True),
            ],
        )
        self.entry_tree.tag_configure("odd", background=CARD)
        self.entry_tree.tag_configure("even", background=CARD_ALT)

        self.gift_card, gift_body, self.gift_badge = self._make_card(
            body, "礼物", "整组累计 >90 钻；显示 ID / 名称 / 数量 / 钻石"
        )
        self.gift_card.grid(row=1, column=1, sticky="nsew")
        self._build_gift_list(gift_body)

    def _make_card(self, parent, title: str, subtitle: str):
        card = tk.Frame(
            parent,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=12, pady=(9, 6))
        tk.Label(
            head, text=title, bg=CARD, fg=FG, font=(FONT, 11, "bold")
        ).pack(side="left")
        tk.Label(
            head, text=subtitle, bg=CARD, fg=MUTED, font=(FONT, 8)
        ).pack(side="left", padx=(9, 0))
        badge = tk.Label(
            head,
            text="0",
            bg="#232b38",
            fg=MUTED,
            font=(FONT, 8, "bold"),
            padx=7,
            pady=1,
        )
        badge.pack(side="right")
        body = tk.Frame(card, bg=CARD)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        return card, body, badge

    def _make_tree(self, parent, columns):
        tree = ttk.Treeview(
            parent,
            columns=[c[0] for c in columns],
            show="headings",
            style="Dark.Treeview",
            selectmode="browse",
        )
        for key, label, width, anchor, stretch in columns:
            tree.heading(key, text=label, anchor=anchor)
            tree.column(key, width=width, anchor=anchor, stretch=stretch, minwidth=40)
        scroll = ttk.Scrollbar(
            parent,
            orient="vertical",
            command=tree.yview,
            style="Dark.Vertical.TScrollbar",
        )
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    # -- 礼物列表（图标 + 送出了 + 名称 × 数量） -------------------------
    def _build_gift_list(self, parent):
        wrap = tk.Frame(parent, bg=CARD)
        wrap.pack(fill="both", expand=True)
        self.gift_canvas = tk.Canvas(
            wrap, bg=CARD, highlightthickness=0, bd=0, takefocus=0
        )
        scroll = ttk.Scrollbar(
            wrap,
            orient="vertical",
            command=self.gift_canvas.yview,
            style="Dark.Vertical.TScrollbar",
        )
        self.gift_canvas.configure(yscrollcommand=scroll.set)
        self.gift_canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.gift_inner = tk.Frame(self.gift_canvas, bg=CARD)
        self._gift_window = self.gift_canvas.create_window(
            (0, 0), window=self.gift_inner, anchor="nw"
        )
        self.gift_inner.bind(
            "<Configure>",
            lambda _e: self.gift_canvas.configure(
                scrollregion=self.gift_canvas.bbox("all")
            ),
        )
        self.gift_canvas.bind(
            "<Configure>",
            lambda e: self.gift_canvas.itemconfigure(
                self._gift_window, width=e.width
            ),
        )
        self.gift_canvas.bind("<MouseWheel>", self._on_gift_wheel)

    def _on_gift_wheel(self, event):
        try:
            self.gift_canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    def _add_gift_row(self, event):
        bg = CARD if self.gift_count % 2 == 0 else CARD_ALT
        row = tk.Frame(self.gift_inner, bg=bg)
        row.pack(fill="x", padx=2, pady=2)
        uid = tk.Label(
            row,
            text=f"ID {event.get('user_id', '')}",
            bg=bg,
            fg=BLUE,
            font=(FONT, 10, "bold"),
            anchor="w",
        )
        uid.pack(side="left")
        name = tk.Label(
            row,
            text=event.get("gift_name", ""),
            bg=bg,
            fg=FG,
            font=(FONT, 11, "bold"),
            anchor="w",
        )
        name.pack(side="left", padx=(10, 0))
        count = tk.Label(
            row,
            text=f"× {event.get('gift_count', 1)}",
            bg=bg,
            fg=ACCENT_DIM,
            font=(FONT, 10, "bold"),
            anchor="w",
        )
        count.pack(side="left", padx=(8, 0))
        diamonds = tk.Label(
            row,
            text=f"{fmt_int(event.get('diamonds', 0))} 钻",
            bg=bg,
            fg=YELLOW,
            font=(FONT, 10, "bold"),
            anchor="e",
        )
        diamonds.pack(side="right", padx=(8, 4))
        for widget in (row, uid, name, count, diamonds):
            widget.bind("<MouseWheel>", self._on_gift_wheel)
        return {
            "frame": row,
            "uid": uid,
            "name": name,
            "count": count,
            "diamonds": diamonds,
        }

    def _update_gift_row(self, info, event):
        info["uid"].configure(text=f"ID {event.get('user_id', '')}")
        info["name"].configure(text=event.get("gift_name", ""))
        info["count"].configure(text=f"× {event.get('gift_count', 1)}")
        info["diamonds"].configure(text=f"{fmt_int(event.get('diamonds', 0))} 钻")
        # 同一组更新后移到底部，保持最新礼物可见
        info["frame"].pack_forget()
        info["frame"].pack(fill="x", padx=2, pady=2)

    def _scroll_gift_bottom(self):
        try:
            self.gift_canvas.update_idletasks()
            self.gift_canvas.yview_moveto(1.0)
        except Exception:
            pass

    # -- 总结 -------------------------------------------------------------
    def _build_summary(self):
        card = tk.Frame(
            self.root,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        card.pack(side="bottom", fill="x", padx=18, pady=(6, 6))
        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=14, pady=(9, 2))
        tk.Label(
            head, text="本场直播总结", bg=CARD, fg=FG, font=(FONT, 11, "bold")
        ).pack(side="left")
        tk.Label(
            head,
            text="涨粉自连接时起算；付费人数按去重发送人统计；双击弹幕可复制完整内容",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 8),
        ).pack(side="left", padx=(10, 0))

        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", padx=8, pady=(4, 10))
        self.summary_vars = {}
        specs = [
            ("online", "当前在线", "人", GREEN),
            ("gain", "本场涨粉", "人", ACCENT_DIM),
            ("payers", "付费人数", "人", BLUE),
            ("gift", "礼物总额", "钻", YELLOW),
            ("pv", "累计观看", "", MUTED),
        ]
        for index, (key, label, unit, color) in enumerate(specs):
            row.grid_columnconfigure(index, weight=1)
            cell = tk.Frame(row, bg=CARD)
            cell.grid(row=0, column=index, sticky="nsew", padx=8)
            tk.Label(
                cell, text=label, bg=CARD, fg=MUTED, font=(FONT, 9)
            ).pack(anchor="w")
            line = tk.Frame(cell, bg=CARD)
            line.pack(anchor="w", pady=(2, 0))
            var = tk.StringVar(value="--")
            self.summary_vars[key] = var
            tk.Label(
                line,
                textvariable=var,
                bg=CARD,
                fg=color,
                font=(FONT, 20, "bold"),
            ).pack(side="left")
            tk.Label(
                line, text=unit, bg=CARD, fg=MUTED, font=(FONT, 9)
            ).pack(side="left", padx=(4, 0), pady=(0, 3))
            if key == "payers":
                self.payer_detail = tk.StringVar(value="")
                tk.Label(
                    cell,
                    textvariable=self.payer_detail,
                    bg=CARD,
                    fg=MUTED,
                    font=(FONT, 8),
                ).pack(anchor="w")

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(side="bottom", fill="x", padx=20, pady=(0, 9))
        self.hint_var = tk.StringVar(value="等待连接")
        tk.Label(
            bar,
            textvariable=self.hint_var,
            bg=BG,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
        ).pack(side="left")
        self.csv_var = tk.StringVar(value="")
        tk.Label(
            bar,
            textvariable=self.csv_var,
            bg=BG,
            fg="#546074",
            font=(FONT, 8),
            anchor="e",
        ).pack(side="right")

    # -- 连接控制 ---------------------------------------------------------
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
        self._clear_tables()
        self.events = queue.Queue()
        cookie = self.cookie_var.get().strip()
        self.client = DanmakuClient(live_id, self.events, cookie=cookie)
        self.client_thread = threading.Thread(target=self.client.run, daemon=True)
        self.client_thread.start()
        self.connect_btn.configure(state="disabled", bg="#6b2434")
        self.disconnect_btn.configure(state="normal", fg=FG)
        self.hint_var.set(f"直播间 {live_id} · 连接中…")
        if cookie:
            self.cookie_hint_var.set("已填 Cookie：尝试接收礼物消息")
        else:
            self.cookie_hint_var.set("未填 Cookie：只能收弹幕，收不到礼物")
        self.csv_var.set("")
        self._set_status("连接中…", YELLOW)

    def disconnect(self):
        client = self.client
        if client is not None:
            client.stop()
            self.client = None
        self.connect_btn.configure(state="normal", bg=ACCENT)
        self.disconnect_btn.configure(state="disabled", fg=MUTED)
        if client is not None:
            self._set_status("已断开", MUTED)

    def on_close(self):
        if self.client is not None:
            self.client.stop()
        self.root.destroy()

    def _clear_tables(self):
        for tree in (self.chat_tree, self.entry_tree):
            for item in tree.get_children():
                tree.delete(item)
        for child in self.gift_inner.winfo_children():
            child.destroy()
        self.chat_count = self.entry_count = self.gift_count = 0
        self.gift_rows.clear()
        self.chat_badge.configure(text="0")
        self.entry_badge.configure(text="0")
        self.gift_badge.configure(text="0")
        for var in self.summary_vars.values():
            var.set("--")
        self.payer_detail.set("")
        self.online_top_var.set("在线 --")

    # -- 事件循环 ---------------------------------------------------------
    def drain_events(self):
        processed = 0
        while processed < 300:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            processed += 1
            try:
                self._handle_event(event)
            except Exception:
                pass
        self.root.after(100, self.drain_events)

    def _handle_event(self, event):
        kind = event.get("kind")
        if kind == "chat":
            level = int(event.get("fans_club_level") or 0)
            pay_level = int(event.get("pay_level") or 0)
            tags = ("odd" if self.chat_count % 2 else "even",)
            if level >= 15:
                tags = tags + ("vip",)
            self._insert(
                self.chat_tree,
                (
                    f"Lv.{pay_level}" if pay_level else "-",
                    f"Lv.{level}" if level else "-",
                    event.get("nickname", ""),
                    event.get("content", ""),
                ),
                tags,
                800,
            )
            self.chat_count += 1
            self.chat_badge.configure(text=fmt_int(self.chat_count))
        elif kind == "entry":
            level = int(event.get("fans_club_level") or 0)
            self._insert(
                self.entry_tree,
                (
                    event.get("ts", ""),
                    f"Lv.{level}" if level else "-",
                    event.get("nickname", ""),
                ),
                ("odd" if self.entry_count % 2 else "even",),
                300,
            )
            self.entry_count += 1
            self.entry_badge.configure(text=fmt_int(self.entry_count))
        elif kind == "gift":
            key = str(event.get("group_key") or event.get("msg_id") or "")
            info = self.gift_rows.get(key) if key else None
            if info:
                self._update_gift_row(info, event)
            else:
                info = self._add_gift_row(event)
                if key:
                    self.gift_rows[key] = info
                self.gift_count += 1
                self.gift_badge.configure(text=fmt_int(self.gift_count))
                self._scroll_gift_bottom()
        elif kind == "summary":
            online = event.get("online")
            if online:
                self.summary_vars["online"].set(fmt_int(online))
                self.online_top_var.set(f"在线 {fmt_int(online)}")
            gain = event.get("follower_gain")
            if gain is not None:
                self.summary_vars["gain"].set(f"+{fmt_int(gain)}")
            payers = event.get("payers")
            if payers is not None:
                self.summary_vars["payers"].set(fmt_int(payers))
                fan_payers = event.get("fan_payers") or 0
                if fan_payers:
                    self.payer_detail.set(f"其中粉丝团 {fan_payers} 人")
                else:
                    self.payer_detail.set("")
            diamonds = event.get("gift_diamonds")
            if diamonds is not None:
                self.summary_vars["gift"].set(fmt_int(diamonds))
            pv = event.get("pv")
            if pv:
                self.summary_vars["pv"].set(str(pv))
            msg_count = event.get("msg_count")
            if msg_count:
                self.hint_var.set(f"已接收 {fmt_int(msg_count)} 条消息")
        elif kind == "status":
            state = event.get("state")
            text = event.get("text", "")
            color = {
                "connected": GREEN,
                "connecting": YELLOW,
                "reconnecting": YELLOW,
                "disconnected": ACCENT_DIM,
                "error": ACCENT,
                "ended": ACCENT_DIM,
                "stopped": MUTED,
            }.get(state, MUTED)
            self._set_status(text, color)
            if state in ("stopped", "ended"):
                self.connect_btn.configure(state="normal", bg=ACCENT)
                self.disconnect_btn.configure(state="disabled", fg=MUTED)
            if state == "connected" and self.client and self.client.csv_path:
                self.csv_var.set(f"CSV: {self.client.csv_path}")
        elif kind == "error":
            self.hint_var.set(event.get("text", ""))

    def _copy_chat(self, event):
        item = self.chat_tree.identify_row(event.y)
        if not item:
            return
        values = self.chat_tree.item(item, "values")
        if len(values) < 4:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(str(values[3]))
            self.hint_var.set("已复制完整弹幕内容")
        except Exception:
            pass

    def _insert(self, tree, values, tags, limit):
        iid = tree.insert("", "end", values=values, tags=tags)
        children = tree.get_children()
        if len(children) > limit:
            for item in children[: len(children) - limit]:
                tree.delete(item)
        children = tree.get_children()
        if children:
            tree.see(children[-1])
        return iid

    def _set_status(self, text, color):
        self.status_var.set(text)
        self.status_dot.itemconfigure(self._dot, fill=color)


def main():
    # PyInstaller --windowed 下 sys.stdout/stderr 为 None，print() 会报错；
    # 改用 console 子系统并隐藏控制台，既保留标准流又让用户看不到黑框。
    if getattr(sys, "frozen", False):
        try:
            import ctypes

            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
        except Exception:
            pass
    if getattr(sys, "frozen", False):
        try:
            log_dir = app_dir() / "logs"
            log_dir.mkdir(exist_ok=True)
            sys.stdout = open(log_dir / "stdout.log", "a", encoding="utf-8", buffering=1)
            sys.stderr = open(log_dir / "stderr.log", "a", encoding="utf-8", buffering=1)

            def _hook(exc_type, exc_value, exc_tb):
                traceback.print_exception(exc_type, exc_value, exc_tb)

            sys.excepthook = _hook
            threading.excepthook = lambda args: _hook(
                args.exc_type, args.exc_value, args.exc_traceback
            )
        except Exception:
            pass
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    if "--export-site" in sys.argv:
        # 供无人值守自检：直接导出 logs 到 site/，不启动窗口。
        import site_builder

        rows = site_builder.load_messages_from_logs(app_dir() / "logs")
        if not rows:
            print("EXPORT_SITE_EMPTY")
            return
        out = app_dir() / "site"
        site_builder.build_site(rows, out, title="抖音弹幕归档")
        print(f"EXPORT_SITE_OK {len(rows)} {out}")
        return
    root = tk.Tk()
    app = DanmakuApp(root)
    # 可选：--room <链接或房间号> 指定默认直播间；--auto-connect 启动后自动连接。
    room_arg = None
    for index, arg in enumerate(sys.argv):
        if arg == "--room" and index + 1 < len(sys.argv):
            room_arg = sys.argv[index + 1]
        elif arg.startswith("--room="):
            room_arg = arg.split("=", 1)[1]
    if room_arg:
        app.room_var.set(room_arg)
    if "--auto-connect" in sys.argv:
        root.after(900, app.connect)
    # 保留引用，避免被回收
    root._app = app
    root.mainloop()


if __name__ == "__main__":
    main()
