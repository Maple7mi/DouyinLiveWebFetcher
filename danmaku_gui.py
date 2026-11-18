#!/usr/bin/env python
# coding: utf-8
"""弹幕助手（Tkinter 桌面窗口）。

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
import re
import sys
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk
from types import SimpleNamespace

import websocket

from collect import parse_live_id
from liveMan import DouyinLiveWebFetcher
from protobuf.douyin import (
    ChatMessage,
    ControlMessage,
    EmojiChatMessage,
    FansclubMessage,
    GiftMessage,
    MemberMessage,
    PushFrame,
    Response,
    RoomMessage,
    RoomStatsMessage,
    RoomUserSeqMessage,
    SocialMessage,
)

# ---------------------------------------------------------------------------
# 主题
# ---------------------------------------------------------------------------
BG = "#f2f5f9"
CARD = "#ffffff"
CARD_ALT = "#f7f9fc"
BORDER = "#dfe5ee"
FG = "#18202c"
MUTED = "#687386"
ACCENT = "#ff2c55"
ACCENT_DIM = "#e04a63"
GREEN = "#0f8a68"
YELLOW = "#b26a00"
BLUE = "#2563c9"
FONT = "Microsoft YaHei UI"
APP_NAME = "弹幕助手"
SESSION_GAP_HOURS = 6
MEMBERSHIP_PRICES = {
    "星守护": 1280,
    "会员": 980,
}
FANCLUB_JOIN_PRICE = 1


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
        # 昵称 -> User，用于 RoomMessage 这类没有 user 字段的付费通知。
        self._nick_users: dict[str, object] = {}
        # 同一条付费通知可能同时出现在 FansclubMessage / RoomMessage 里。
        self._membership_seen: dict[str, float] = {}
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
        self.session_date = datetime.now().strftime("%Y-%m-%d")
        self._session_started_at = datetime.now().isoformat(timespec="seconds")
        self._last_session_save = 0.0

    # -- 生命周期 ---------------------------------------------------------
    def run(self):
        self._open_logs()
        self._capture_room_meta()
        try:
            while not self._stop.is_set():
                if not self._wait_until_live():
                    break
                self._maybe_roll_session_date()
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

    def _maybe_roll_session_date(self):
        """等待了一整天后再开播：新开播日应另起一页，而不是沿用昨天。"""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if self.session_date == today:
            return
        try:
            old = datetime.strptime(self.session_date, "%Y-%m-%d").date()
        except ValueError:
            old = now.date()
        if (now.date() - old).days >= 1 and now.hour >= 6:
            self.session_date = today
            self._session_started_at = now.isoformat(timespec="seconds")
            self._save_session_state(force=True)

    def _wait_until_live(self) -> bool:
        """未开播时保持等待，开播后再建立 WebSocket。"""
        while not self._stop.is_set():
            live = self.is_room_live()
            if live is not False:
                return True
            self._emit("status", state="waiting", text="等待开播…")
            if self._stop.wait(5):
                return False
        return False

    def stop(self):
        self._stop.set()
        ws = getattr(self, "ws", None)
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _session_state_path(self) -> Path:
        return app_dir() / "logs" / f"session_state_{self.live_id}.json"

    def _resolve_session_date(self) -> str:
        """同一场直播跨午夜时，沿用开播当天的日期。"""
        now = datetime.now()
        try:
            state = json.loads(
                self._session_state_path().read_text(encoding="utf-8-sig")
            )
            last = datetime.fromisoformat(state["last_event_at"])
            if (
                state.get("session_date")
                and now - last <= timedelta(hours=SESSION_GAP_HOURS)
            ):
                self._session_started_at = state.get(
                    "started_at", self._session_started_at
                )
                return str(state["session_date"])
        except Exception:
            pass
        return now.strftime("%Y-%m-%d")

    def _save_session_state(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_session_save < 30:
            return
        self._last_session_save = now
        try:
            path = self._session_state_path()
            path.parent.mkdir(exist_ok=True)
            state = {
                "live_id": self.live_id,
                "session_date": self.session_date,
                "started_at": self._session_started_at,
                "last_event_at": datetime.now().isoformat(timespec="seconds"),
            }
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(path)
        except Exception:
            pass

    def _open_logs(self):
        logs = app_dir() / "logs"
        logs.mkdir(exist_ok=True)
        self.session_date = self._resolve_session_date()
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
                "session_date",
            ]
        )
        self._csv_file.flush()
        self._save_session_state(force=True)
        self._gift_raw = (logs / f"gifts_raw_{self.live_id}_{stamp}.jsonl").open(
            "w", encoding="utf-8"
        )

    def _capture_room_meta(self):
        """记录主播昵称/房间号，供导出网站读取；失败不影响采集。"""
        try:
            meta = self.fetch_room_meta()
            self._debug(f"room meta: {meta!r}")
            if not meta.get("anchor_name"):
                return
            path = app_dir() / "logs" / f"room_meta_{self.live_id}.json"
            path.parent.mkdir(exist_ok=True)
            path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._debug(f"room meta saved: {path}")
        except Exception as exc:
            self._debug(f"room meta failed: {exc!r}")

    def _close_logs(self):
        self._save_session_state(force=True)
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
                    self.session_date,
                ]
            )
            self._csv_file.flush()
            self._save_session_state()
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
            "WebcastRoomMessage": self._parseRoomMsg,
            "WebcastRoomRankMessage": self._parseRankMsg,
        }
        for msg in response.messages_list:
            handler = handlers.get(msg.method)
            if not handler:
                # 会员/星守护有时走未建模的消息类型；只在原文含关键词时兜底。
                try:
                    self._parse_unknown_membership(msg.method, msg.payload)
                except Exception:
                    pass
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
        self._remember_user(user)
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
        self._remember_user(user)
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
        self._remember_user(user)
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
        self._remember_user(user)
        msg_id = str(message.common.msg_id or "")
        repeat = int(message.repeat_count or 0)
        combo = int(message.combo_count or 0)
        group = int(message.group_id or 0)
        total_count = int(message.total_count or 0)
        unit = int(gift.diamond_count or 0)
        if unit <= 0:
            for name, value in MEMBERSHIP_PRICES.items():
                if name in (gift.name or ""):
                    unit = value
                    break

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

    def _remember_user(self, user):
        """缓存昵称到 User；RoomMessage 没有 user 字段，只能靠昵称回查。"""
        if not user:
            return
        nick = str(getattr(user, "nick_name", "") or "").strip()
        if not nick:
            return
        self._nick_users[nick] = user
        if len(self._nick_users) > 800:
            for old in list(self._nick_users)[:200]:
                self._nick_users.pop(old, None)

    @staticmethod
    def _notice_user(nick: str):
        nick = str(nick or "").strip(" \t\r\n：:，,。.!！[]【】()（）")
        if not nick:
            return None
        return SimpleNamespace(
            id=f"notice:{nick}",
            nick_name=nick,
            fans_club=None,
            pay_grade=None,
        )

    def _find_notice_user(self, text: str):
        """从「恭喜 XXX 开通了…」里找昵称，优先命中已缓存的 User。"""
        text = str(text or "").strip()
        if not text:
            return None
        match = re.search(
            r"恭喜\s*(.+?)\s*(?:成为|开通|加入|成功|续费|升级|的)", text
        )
        if match:
            nick = match.group(1).strip()
            return self._nick_users.get(nick) or self._notice_user(nick)
        # 没有「恭喜」时，尝试取「XXX 开通了…」里的昵称。
        loose = re.search(
            r"([^\s，,：:。.!！]{2,24})\s*(?:开通|续费|加入|成为)", text
        )
        if loose:
            nick = re.sub(r"^(?:恭喜|欢迎)", "", loose.group(1)).strip()
            return self._nick_users.get(nick) or self._notice_user(nick)
        for nick in sorted(self._nick_users, key=len, reverse=True):
            if nick and nick in text:
                return self._nick_users[nick]
        return None

    def _parse_unknown_membership(self, method: str, payload: bytes):
        """未建模消息里偶尔夹带会员/星守护提示，按原文关键词兜底。"""
        try:
            raw = payload.decode("utf-8", errors="ignore")
        except Exception:
            return
        if not any(
            key in raw
            for key in ("星守护", "会员", "加入粉丝团", "加入了粉丝团")
        ):
            return
        content = ""
        user = None
        try:
            if "Fansclub" in method:
                message = FansclubMessage().parse(payload)
                content = message.content
                user = message.user
            else:
                message = RoomMessage().parse(payload)
                content = message.content
        except Exception:
            return
        if not content:
            return
        if user is None:
            user = self._notice_member_user(content)
        self._remember_user(user)
        uid = str(getattr(user, "id", "") or "")
        # 未知消息没有可靠 msg_id：用内容 + 用户 + 10 秒窗口去重。
        dedupe_id = f"notice-{uid}-{abs(hash(content))}-{int(time.time() // 10)}"
        self._record_membership(
            content=content,
            msg_id=dedupe_id,
            user=user,
        )

    def _notice_member_user(self, text: str):
        user = self._find_notice_user(text)
        if user:
            return user
        if any(
            key in text
            for key in ("星守护", "会员", "加入粉丝团", "加入了粉丝团")
        ):
            # 实在没有昵称也先记一笔，避免整条付费通知被静默丢掉。
            return self._notice_user("直播间通知")
        return None

    def _parseFansclubMsg(self, payload):
        message = FansclubMessage().parse(payload)
        self._remember_user(message.user)
        self._record_membership(
            content=message.content,
            msg_id=str(message.common_info.msg_id or ""),
            user=message.user,
        )

    def _parseRoomMsg(self, payload):
        message = RoomMessage().parse(payload)
        self._record_membership(
            content=message.content,
            msg_id=str(message.common.msg_id or ""),
            user=self._notice_member_user(message.content),
        )

    @staticmethod
    def _membership_price(text: str, base: int) -> int:
        """尽量从消息文案里读出实际价格/月数；读不到才用基础价估算。"""
        explicit = re.search(r"(\d{2,6})\s*(?:钻|钻石|抖币)", text)
        if explicit:
            return int(explicit.group(1))
        months = re.search(r"(\d+)\s*个?\s*月", text)
        if months:
            return base * max(1, int(months.group(1)))
        days = re.search(r"(\d+)\s*天", text)
        if days and "星守护" not in text and "会员" not in text:
            return max(1, round(base * int(days.group(1)) / 30))
        # 星守护/会员都是按月/31 天订阅，文案里的「31天」不改变单价。
        return base

    def _record_membership(self, content: str, msg_id: str, user):
        """星守护/直播会员/加入粉丝团按付费事件补录。"""
        text = str(content or "").strip()
        label = ""
        price = 0
        # 普通粉丝团：可能是「加入了粉丝团」，也可能是「恭喜 X 成为第 N 名 Y 成员」。
        fan_join = (
            "加入粉丝团" in text
            or "加入了粉丝团" in text
            or (
                "成为第" in text
                and "成员" in text
                and ("恭喜" in text or "欢迎" in text)
            )
        )
        if fan_join and not any(name in text for name in MEMBERSHIP_PRICES):
            label = "加入粉丝团"
            price = FANCLUB_JOIN_PRICE
        else:
            for name, base in MEMBERSHIP_PRICES.items():
                if name in text:
                    label = name
                    price = self._membership_price(text, base)
                    break
        if not label or not price or not user or not user.id:
            return
        # FansclubMessage 与 RoomMessage 可能各推一条同样的通知，5 秒内按文案去重。
        dedupe = f"{label}|{text}"
        now = time.monotonic()
        if now - self._membership_seen.get(dedupe, 0.0) < 5:
            return
        self._membership_seen[dedupe] = now
        if not self._once("gift", msg_id):
            return
        club = user.fans_club.data if user.fans_club else None
        level = int(club.level or 0) if club else 0
        pay_level = int(user.pay_grade.level or 0) if user.pay_grade else 0
        uid = str(user.id)
        self._payers.add(uid)
        if level > 0:
            self._fan_payers.add(uid)
        self._gift_diamonds += price
        self._msg_count += 1
        data = dict(
            msg_id=msg_id,
            user_id=uid,
            nickname=user.nick_name,
            fans_club_level=level,
            pay_level=pay_level,
            content=text or label,
            gift_name=label,
            gift_count=1,
            diamonds=price,
            group_id="",
            visible=1 if price > 90 else 0,
            group_key=msg_id or f"member-{uid}-{int(time.time())}",
        )
        self._write_csv("gift", **data)
        if price > 90:
            self._emit("gift", **data)
        self._emit_summary()

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
        self.root.title(f"{APP_NAME} · 实时弹幕 / 入场 / 礼物 / 直播总结")
        self.root.geometry("1600x1000")
        self.root.minsize(1280, 800)
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
            "Light.Treeview",
            background=CARD,
            fieldbackground=CARD,
            foreground=FG,
            borderwidth=0,
            rowheight=36,
            font=(FONT, 10),
        )
        style.configure(
            "Light.Treeview.Heading",
            background="#edf2f8",
            foreground="#4b5565",
            borderwidth=0,
            relief="flat",
            font=(FONT, 10, "bold"),
            padding=(7, 8),
        )
        style.map(
            "Light.Treeview",
            background=[("selected", "#dbe8ff")],
            foreground=[("selected", "#0b1f3a")],
        )
        style.map(
            "Light.Treeview.Heading",
            background=[("active", "#e2e9f3")],
        )
        style.configure(
            "Light.Vertical.TScrollbar",
            background="#cbd5e1",
            darkcolor="#cbd5e1",
            lightcolor="#cbd5e1",
            troughcolor="#f4f7fb",
            bordercolor="#f4f7fb",
            arrowcolor="#64748b",
            relief="flat",
            borderwidth=0,
        )
        style.map(
            "Light.Vertical.TScrollbar",
            background=[("active", "#b6c2d1")],
        )

    # -- 顶部 -------------------------------------------------------------
    def _build_topbar(self):
        bar = tk.Frame(
            self.root,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        bar.pack(fill="x", padx=18, pady=(14, 10))

        icon = tk.Label(
            bar,
            text="弹",
            bg=ACCENT,
            fg="#ffffff",
            font=(FONT, 15, "bold"),
            width=2,
            height=1,
        )
        icon.pack(side="left", padx=(14, 10), pady=12)

        brand = tk.Frame(bar, bg=CARD)
        brand.pack(side="left", pady=10)
        tk.Label(
            brand,
            text=APP_NAME,
            bg=CARD,
            fg=FG,
            font=(FONT, 19, "bold"),
        ).pack(anchor="w")
        tk.Label(
            brand,
            text="实时弹幕 · 粉丝团灯牌 · 礼物 · 直播总结",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
        ).pack(anchor="w", pady=(1, 0))

        right = tk.Frame(bar, bg=CARD)
        right.pack(side="right", padx=(0, 14))
        self.status_dot = tk.Canvas(
            right, width=10, height=10, bg=CARD, highlightthickness=0
        )
        self.status_dot.pack(side="left", padx=(0, 6))
        self._dot = self.status_dot.create_oval(1, 1, 9, 9, fill=MUTED, outline="")
        self.status_var = tk.StringVar(value="未连接")
        tk.Label(
            right,
            textvariable=self.status_var,
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
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
        controls.pack(side="right", padx=(0, 24))
        self.room_var = tk.StringVar(value="https://live.douyin.com/77217772750")
        entry_border = tk.Frame(controls, bg=BORDER)
        entry_border.pack(side="left", padx=(0, 8))
        entry = tk.Entry(
            entry_border,
            textvariable=self.room_var,
            bg=CARD_ALT,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=(FONT, 10),
            width=40,
        )
        entry.pack(ipady=8, padx=1, pady=1)
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
            disabledforeground="#8a1f31",
        )
        self.connect_btn.pack(side="left", padx=(0, 6))
        self.disconnect_btn = tk.Button(
            controls,
            text="断开",
            command=self.disconnect,
            bg="#f1f5f9",
            fg=MUTED,
            activebackground="#e2e8f0",
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
            bg="#f1f5f9",
            fg=BLUE,
            activebackground="#e2e8f0",
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
        bar = tk.Frame(
            self.root,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        bar.pack(fill="x", padx=18, pady=(0, 8))

        tk.Label(
            bar,
            text="Cookie",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9, "bold"),
        ).pack(side="left", padx=(14, 0))
        tk.Label(
            bar,
            text="登录抖音网页版后复制；填了才能收到礼物消息",
            bg=CARD,
            fg="#6f7a8c",
            font=(FONT, 8),
        ).pack(side="left", padx=(6, 8))

        self.cookie_var = tk.StringVar()
        entry_wrap = tk.Frame(bar, bg=BORDER)
        entry_wrap.pack(side="left", fill="x", expand=True, pady=6)
        self.cookie_entry = tk.Entry(
            entry_wrap,
            textvariable=self.cookie_var,
            bg=CARD_ALT,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=(FONT, 9),
            show="•",
        )
        self.cookie_entry.pack(fill="x", ipady=5, padx=1, pady=1)

        tk.Button(
            bar,
            text="读取 cookie.txt",
            command=self._load_cookie_file,
            bg="#f1f5f9",
            fg=MUTED,
            activebackground="#e2e8f0",
            activeforeground=FG,
            relief="flat",
            bd=0,
            font=(FONT, 8),
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=(6, 4), pady=6)
        tk.Button(
            bar,
            text="保存",
            command=self._save_cookie_file,
            bg="#f1f5f9",
            fg=MUTED,
            activebackground="#e2e8f0",
            activeforeground=FG,
            relief="flat",
            bd=0,
            font=(FONT, 8),
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=(0, 4), pady=6)
        tk.Button(
            bar,
            text="清除",
            command=self._clear_cookie,
            bg="#f1f5f9",
            fg=MUTED,
            activebackground="#e2e8f0",
            activeforeground=FG,
            relief="flat",
            bd=0,
            font=(FONT, 8),
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left", pady=6)

        self.cookie_hint_var = tk.StringVar(value="未填：只能收弹幕，收不到礼物")
        tk.Label(
            bar,
            textvariable=self.cookie_hint_var,
            bg=CARD,
            fg="#6f7a8c",
            font=(FONT, 8),
        ).pack(side="left", padx=(8, 12))

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
            body,
            "弹幕",
            "全部聊天 · ★ 消费>35 或 粉丝团≥15 高亮 · 🔥 消费>40 或 粉丝团≥18 超级高亮",
        )
        self.chat_card.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 10))
        self.chat_tree = self._make_tree(
            chat_body,
            [
                ("pay", "消费等级", 92, "center", False),
                ("level", "粉丝团", 82, "center", False),
                ("nick", "昵称", 180, "w", False),
                ("content", "弹幕内容", 560, "w", True),
            ],
        )
        self.chat_tree.tag_configure("odd", background=CARD)
        self.chat_tree.tag_configure("even", background=CARD_ALT)
        self.chat_tree.tag_configure(
            "hot",
            background="#fff1c9",
            foreground="#5a4300",
            font=(FONT, 10, "bold"),
        )
        self.chat_tree.tag_configure(
            "super",
            background="#ffdede",
            foreground="#8f0018",
            font=(FONT, 12, "bold"),
        )
        self.chat_tree.bind("<Double-1>", self._copy_chat)
        self.chat_placeholder = tk.Label(
            chat_body,
            text="等待弹幕…",
            bg=CARD,
            fg="#94a3b8",
            font=(FONT, 10),
        )
        self.chat_placeholder.place(relx=0.5, rely=0.5, anchor="center")

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
        self.entry_placeholder = tk.Label(
            entry_body,
            text="等待 15 级以上粉丝团入场…",
            bg=CARD,
            fg="#94a3b8",
            font=(FONT, 9),
        )
        self.entry_placeholder.place(relx=0.5, rely=0.55, anchor="center")

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
        head.pack(fill="x", padx=12, pady=(10, 6))
        tk.Frame(head, bg=ACCENT, width=3, height=16).pack(side="left", padx=(0, 7))
        tk.Label(
            head, text=title, bg=CARD, fg=FG, font=(FONT, 11, "bold")
        ).pack(side="left")
        tk.Label(
            head, text=subtitle, bg=CARD, fg=MUTED, font=(FONT, 8)
        ).pack(side="left", padx=(9, 0))
        badge = tk.Label(
            head,
            text="0",
            bg="#eef2f7",
            fg="#475569",
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
            style="Light.Treeview",
            selectmode="browse",
        )
        for key, label, width, anchor, stretch in columns:
            tree.heading(key, text=label, anchor=anchor)
            tree.column(key, width=width, anchor=anchor, stretch=stretch, minwidth=40)
        scroll = tk.Scrollbar(
            parent,
            orient="vertical",
            command=tree.yview,
            bg="#cbd5e1",
            troughcolor="#f4f7fb",
            activebackground="#b6c2d1",
            highlightthickness=0,
            bd=0,
            relief="flat",
            width=12,
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
        scroll = tk.Scrollbar(
            wrap,
            orient="vertical",
            command=self.gift_canvas.yview,
            bg="#cbd5e1",
            troughcolor="#f4f7fb",
            activebackground="#b6c2d1",
            highlightthickness=0,
            bd=0,
            relief="flat",
            width=12,
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
        self.gift_placeholder = None
        self._show_gift_placeholder()

    def _show_gift_placeholder(self):
        if self.gift_placeholder is not None:
            return
        self.gift_placeholder = tk.Label(
            self.gift_inner,
            text="高价值礼物会显示在这里\n单条 / 整组累计 > 90 钻",
            bg=CARD,
            fg="#94a3b8",
            font=(FONT, 9),
            justify="center",
        )
        self.gift_placeholder.pack(pady=34)

    def _on_gift_wheel(self, event):
        try:
            self.gift_canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    def _add_gift_row(self, event):
        if self.gift_placeholder is not None:
            self.gift_placeholder.destroy()
            self.gift_placeholder = None
        bg = CARD if self.gift_count % 2 == 0 else CARD_ALT
        row = tk.Frame(
            self.gift_inner,
            bg=bg,
            highlightbackground="#eef2f7",
            highlightthickness=1,
        )
        row.pack(fill="x", padx=8, pady=3)
        uid = tk.Label(
            row,
            text=f"ID {event.get('user_id', '')}",
            bg=bg,
            fg=BLUE,
            font=(FONT, 11, "bold"),
            anchor="w",
            pady=3,
        )
        uid.pack(side="left", padx=(8, 0))
        name = tk.Label(
            row,
            text=event.get("gift_name", ""),
            bg=bg,
            fg=FG,
            font=(FONT, 12, "bold"),
            anchor="w",
            pady=3,
        )
        name.pack(side="left", padx=(10, 0))
        count = tk.Label(
            row,
            text=f"× {event.get('gift_count', 1)}",
            bg=bg,
            fg=ACCENT_DIM,
            font=(FONT, 11, "bold"),
            anchor="w",
            pady=3,
        )
        count.pack(side="left", padx=(8, 0))
        diamonds = tk.Label(
            row,
            text=f"{fmt_int(event.get('diamonds', 0))} 钻",
            bg=bg,
            fg=YELLOW,
            font=(FONT, 11, "bold"),
            anchor="e",
            pady=3,
        )
        diamonds.pack(side="right", padx=(8, 8))
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
        info["frame"].pack(fill="x", padx=8, pady=3)

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
        row.pack(fill="x", padx=8, pady=(4, 12))
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
            cell = tk.Frame(
                row,
                bg="#f8fafc",
                highlightbackground="#eef2f7",
                highlightthickness=1,
            )
            cell.grid(row=0, column=index, sticky="nsew", padx=4)
            inner = tk.Frame(cell, bg="#f8fafc")
            inner.pack(fill="both", expand=True, padx=12, pady=8)
            tk.Frame(inner, bg=color, width=24, height=3).pack(anchor="w")
            tk.Label(
                inner, text=label, bg="#f8fafc", fg=MUTED, font=(FONT, 9)
            ).pack(anchor="w", pady=(5, 0))
            line = tk.Frame(inner, bg="#f8fafc")
            line.pack(anchor="w", pady=(1, 0))
            var = tk.StringVar(value="--")
            self.summary_vars[key] = var
            tk.Label(
                line,
                textvariable=var,
                bg="#f8fafc",
                fg=color,
                font=(FONT, 22, "bold"),
            ).pack(side="left")
            tk.Label(
                line, text=unit, bg="#f8fafc", fg=MUTED, font=(FONT, 9)
            ).pack(side="left", padx=(4, 0), pady=(0, 4))
            if key == "payers":
                self.payer_detail = tk.StringVar(value="")
                tk.Label(
                    inner,
                    textvariable=self.payer_detail,
                    bg="#f8fafc",
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
            fg="#6b7586",
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
        self.connect_btn.configure(
            state="disabled", bg="#fef3c7", fg="#92400e", text="连接中…"
        )
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
        self.connect_btn.configure(
            state="normal", bg=ACCENT, fg="#ffffff", text="连接"
        )
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
        self.gift_placeholder = None
        self._show_gift_placeholder()
        self.chat_count = self.entry_count = self.gift_count = 0
        self.gift_rows.clear()
        self.chat_badge.configure(text="0")
        self.entry_badge.configure(text="0")
        self.gift_badge.configure(text="0")
        if self.entry_placeholder is not None:
            self.entry_placeholder.place(relx=0.5, rely=0.55, anchor="center")
        if self.chat_placeholder is not None:
            self.chat_placeholder.place(relx=0.5, rely=0.5, anchor="center")
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
            if self.chat_placeholder is not None:
                self.chat_placeholder.place_forget()
            level = int(event.get("fans_club_level") or 0)
            pay_level = int(event.get("pay_level") or 0)
            if pay_level > 40 or level >= 18:
                tags = ("super",)
            elif pay_level > 35 or level >= 15:
                tags = ("hot",)
            else:
                tags = ("odd" if self.chat_count % 2 else "even",)
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
            if self.entry_placeholder is not None:
                self.entry_placeholder.place_forget()
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
            if state == "connected":
                self.connect_btn.configure(
                    state="disabled", bg="#dcfce7", fg="#15803d", text="已连接"
                )
                self.disconnect_btn.configure(state="normal", fg=FG)
            elif state in ("connecting", "reconnecting"):
                self.connect_btn.configure(
                    state="disabled", bg="#fef3c7", fg="#92400e", text="连接中…"
                )
            elif state in ("stopped", "ended", "disconnected", "error"):
                self.connect_btn.configure(
                    state="normal", bg=ACCENT, fg="#ffffff", text="连接"
                )
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
