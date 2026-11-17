#!/usr/bin/env python
# coding: utf-8
"""把弹幕姬日志导出成按日期分类的静态网站。

用法：
    python site_builder.py --logs logs --out site
    python site_builder.py --mock --out site_preview

网站是纯静态 HTML/CSS/JS，直接双击 index.html 就能看，不需要服务器。
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import random
import re
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

KIND_LABELS = {
    "chat": "弹幕",
    "gift": "礼物",
    "entry": "入场",
}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return default


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalize(row: dict[str, Any]) -> dict[str, Any] | None:
    received = _clean(row.get("received_at"))
    if not received:
        return None
    kind = _clean(row.get("kind")).lower()
    if kind == "member":
        kind = "entry"
    if kind not in KIND_LABELS:
        return None
    return {
        "received_at": received,
        "kind": kind,
        "msg_id": _clean(row.get("msg_id")),
        "user_id": _clean(row.get("user_id")),
        "nickname": _clean(row.get("nickname")),
        "fans_club_level": _int(row.get("fans_club_level")),
        "pay_level": _int(row.get("pay_level")),
        "content": _clean(row.get("content")),
        "gift_name": _clean(row.get("gift_name")),
        "gift_count": _int(row.get("gift_count")),
        "diamonds": _int(row.get("diamonds")),
        "group_id": _clean(row.get("group_id")),
        "visible": _int(row.get("visible")),
    }


def _load_raw_gifts(
    log_dir: Path,
    user_meta: dict[str, dict[str, Any]],
    live_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """旧版 CSV 只记录 >90 钻礼物；从 gifts_raw_*.jsonl 还原全部礼物。

    连击消息的 repeat_count 是累计值，按 group_id 取最大值作为整组数量。
    """
    result: dict[str, dict[str, Any]] = {}
    pattern = f"gifts_raw_{live_id}_*.jsonl" if live_id else "gifts_raw_*.jsonl"
    for path in sorted(log_dir.glob(pattern)):
        groups: dict[str, dict[str, Any]] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in lines:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ts = str(obj.get("ts") or "").replace("T", " ")[:19]
            if not ts:
                continue
            uid = str(obj.get("user_id") or "")
            name = _clean(obj.get("gift_name"))
            unit = _int(obj.get("unit_diamond"))
            repeat = max(_int(obj.get("repeat_count")), 1)
            msg_id = str(obj.get("msg_id") or "")
            gid = str(obj.get("group_id") or "")
            count = repeat
            if gid and gid != "0":
                item = groups.setdefault(
                    gid,
                    {
                        "date": ts[:10],
                        "user_id": uid,
                        "gift_name": name,
                        "unit": unit,
                        "count": 1,
                        "msg_id": msg_id,
                        "received_at": ts,
                        "group_id": gid,
                    },
                )
                item["count"] = max(item["count"], repeat)
                item["msg_id"] = msg_id
            else:
                count = repeat
                row = {
                    "received_at": ts,
                    "kind": "gift",
                    "msg_id": msg_id,
                    "user_id": uid,
                    "nickname": "",
                    "fans_club_level": 0,
                    "pay_level": 0,
                    "content": name,
                    "gift_name": name,
                    "gift_count": count,
                    "diamonds": unit * count,
                    "group_id": "",
                    "visible": 1 if unit * count > 90 else 0,
                }
                key = f"m|{ts[:10]}|{msg_id}|{uid}"
                old = result.get(key)
                if old is None or row["diamonds"] > old["diamonds"]:
                    result[key] = row
        for gid, item in groups.items():
            count = max(item["count"], 1)
            row = {
                "received_at": item["received_at"],
                "kind": "gift",
                "msg_id": item["msg_id"],
                "user_id": item["user_id"],
                "nickname": "",
                "fans_club_level": 0,
                "pay_level": 0,
                "content": item["gift_name"],
                "gift_name": item["gift_name"],
                "gift_count": count,
                "diamonds": item["unit"] * count,
                "group_id": gid,
                "visible": 1 if item["unit"] * count > 90 else 0,
            }
            key = f"g|{item['date']}|{gid}|{item['user_id']}"
            old = result.get(key)
            if old is None or row["diamonds"] > old["diamonds"]:
                result[key] = row
    # 用 CSV 里的聊天资料补昵称/等级（raw 礼物消息本身不带昵称）
    for row in result.values():
        meta = user_meta.get(row["user_id"])
        if meta:
            row["nickname"] = meta.get("nickname", "")
            row["fans_club_level"] = meta.get("fans_club_level", 0)
            row["pay_level"] = meta.get("pay_level", 0)
    return result


def load_room_meta(log_dir: str | Path, live_id: str | None = None) -> dict[str, Any]:
    """读取采集端保存的主播元数据；没有就返回空。"""
    if not live_id:
        return {}
    path = Path(log_dir) / f"room_meta_{live_id}.json"
    if not path.exists():
        return {}
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return meta if isinstance(meta, dict) else {}


def _room_meta_html(meta: dict[str, Any], live_id: str | None) -> str:
    anchor = _clean(meta.get("anchor_name")) or "未记录主播"
    room = _clean(meta.get("live_id") or live_id) or "-"
    title = _clean(meta.get("title"))
    parts = [f"主播：<b>{html.escape(anchor)}</b>", f"房间号：<b>{html.escape(room)}</b>"]
    if title:
        parts.append(html.escape(title))
    return " · ".join(parts)


def _deleted_days_file(log_dir: str | Path, live_id: str | None) -> Path:
    return Path(log_dir) / f"deleted_days_{live_id or 'all'}.json"


def load_deleted_days(log_dir: str | Path, live_id: str | None) -> set[str]:
    path = _deleted_days_file(log_dir, live_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(data, list):
        return set()
    return {str(day) for day in data if len(str(day)) == 10}


def mark_day_deleted(log_dir: str | Path, live_id: str | None, day: str) -> None:
    days = load_deleted_days(log_dir, live_id)
    days.add(day)
    path = _deleted_days_file(log_dir, live_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sorted(days), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_messages_from_logs(
    log_dir: str | Path, live_id: str | None = None
) -> list[dict[str, Any]]:
    """读取 gui_*.csv + gifts_raw_*.jsonl，按 msg_id/group_id 去重并按时间排序。

    传入 live_id 时只读取该直播间的日志，避免多个直播间的数据混在同一天里。
    """
    log_dir = Path(log_dir)
    if not log_dir.exists():
        return []

    chat_best: dict[str, dict[str, Any]] = {}
    gift_best: dict[str, dict[str, Any]] = {}
    user_meta: dict[str, dict[str, Any]] = {}
    anon = 0

    csv_pattern = f"gui_{live_id}_*.csv" if live_id else "gui_*.csv"
    for path in sorted(log_dir.glob(csv_pattern)):
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as fh:
                for raw in csv.DictReader(fh):
                    row = _normalize(raw)
                    if not row:
                        continue
                    if row["user_id"]:
                        user_meta[row["user_id"]] = {
                            "nickname": row["nickname"],
                            "fans_club_level": row["fans_club_level"],
                            "pay_level": row["pay_level"],
                        }
                    if row["kind"] == "gift":
                        key = (
                            f"g|{row['received_at'][:10]}|{row['group_id']}|{row['user_id']}"
                            if row["group_id"]
                            else f"m|{row['received_at'][:10]}|{row['msg_id']}|{row['user_id']}"
                        )
                        old = gift_best.get(key)
                        # 同一连击会写多行，保留整组累计最大的一行
                        if old is None or (
                            row["diamonds"], row["gift_count"], row["received_at"]
                        ) >= (
                            old["diamonds"], old["gift_count"], old["received_at"]
                        ):
                            gift_best[key] = row
                    else:
                        key = row["msg_id"] or f"anon-{anon}"
                        anon += 1
                        chat_best.setdefault(key, row)
        except Exception:
            continue

    # 从 raw JSONL 还原全部礼物（含低于 90 钻的），与 CSV 礼物按 group 去重。
    for key, row in _load_raw_gifts(log_dir, user_meta, live_id).items():
        old = gift_best.get(key)
        if old is None or row["diamonds"] > old["diamonds"]:
            gift_best[key] = row

    rows = list(chat_best.values()) + list(gift_best.values())
    deleted_days = load_deleted_days(log_dir, live_id)
    if deleted_days:
        rows = [r for r in rows if r["received_at"][:10] not in deleted_days]
    rows.sort(key=lambda r: (r["received_at"], r["msg_id"]))
    return rows


# ---------------------------------------------------------------------------
# 模拟数据：两天，能看出按日期分类 + 当日礼物榜
# ---------------------------------------------------------------------------
MOCK_USERS = [
    ("1839201746", "晚风", 16, 28),
    ("9274016385", "小满", 11, 12),
    ("5382019476", "一颗糖", 20, 35),
    ("6628041937", "阿梨", 8, 6),
    ("7510392846", "北岛", 14, 19),
    ("2901847365", "橘子汽水", 6, 3),
    ("8473620195", "失眠的猫", 17, 24),
    ("3192847560", "月亮邮差", 9, 8),
    ("6081729354", "不熬夜", 3, 2),
    ("4820193756", "山茶", 22, 41),
    ("9157302846", "小鹿乱撞", 12, 15),
    ("7364820195", "阿远", 5, 4),
    ("2847560193", "南风知我意", 19, 30),
    ("5739201846", "有点困", 7, 5),
    ("8492017365", "桃桃", 15, 18),
    ("6201938475", "晚八点", 2, 1),
    ("3918475620", "人间清醒", 25, 52),
    ("7582019364", "小透明", 0, 0),
    ("8047362915", "风吹麦浪", 13, 17),
    ("1937562840", "拾光", 10, 9),
]

MOCK_PHRASES = [
    "这个皮肤好好看",
    "主播今天状态不错啊",
    "来了来了",
    "刚下班，赶上了",
    "这波操作可以",
    "哈哈哈哈哈哈",
    "别浪别浪",
    "稳住，能赢",
    "今天还打几把？",
    "这个英雄我不太会",
    "对面打野来了",
    "小心草丛",
    "冲冲冲",
    "主播声音好好听",
    "关注了，每天都来",
    "这局稳了",
    "好家伙，直接反杀",
    "队友呢，救一下啊",
    "笑死我了",
    "再来一把",
    "晚上好呀",
    "刚进来，发生了什么",
    "这波不亏",
    "细节拉满",
    "666",
    "太秀了",
    "主播别下播啊",
    "再唱一首",
    "今天人好多",
    "灯牌已点亮",
]

MOCK_GIFTS = [
    ("小心心", 1),
    ("粉丝团灯牌", 1),
    ("玫瑰", 1),
    ("人气票", 1),
    ("大啤酒", 2),
    ("加油鸭", 5),
    ("爱的抱抱", 99),
    ("跑车", 1200),
    ("豪华跑车", 1200),
    ("嘉年华", 30000),
    ("抖音1号", 10001),
    ("私人飞机", 3000),
    ("飞机", 2999),
    ("浪漫马车", 28888),
]


def mock_messages() -> list[dict[str, Any]]:
    rng = random.Random(20261117)
    result: list[dict[str, Any]] = []
    days = ["2026-11-15", "2026-11-16", "2026-11-17"]

    for day_index, day in enumerate(days):
        base = datetime.strptime(day, "%Y-%m-%d").replace(hour=19, minute=0, second=0)
        cursor = 0
        seq = 0

        def add(kind: str, user: tuple, **extra: Any) -> None:
            nonlocal cursor, seq
            cursor += rng.randint(18, 75)
            seq += 1
            when = (base + timedelta(seconds=cursor)).strftime("%Y-%m-%d %H:%M:%S")
            uid, nick, fan, pay = user
            row = {
                "received_at": when,
                "kind": kind,
                "msg_id": f"mock-{day}-{seq:04d}",
                "user_id": uid,
                "nickname": nick,
                "fans_club_level": fan,
                "pay_level": pay,
                "content": "",
                "gift_name": "",
                "gift_count": 0,
                "diamonds": 0,
                "group_id": "",
                "visible": 0,
            }
            row.update(extra)
            result.append(row)

        # 开场入场
        for user in rng.sample(MOCK_USERS, 6):
            add("entry", user, content="进入直播间")

        # 30 条聊天，穿插礼物
        gift_plan = [
            (MOCK_USERS[9], "嘉年华", 1),
            (MOCK_USERS[0], "跑车", 2),
            (MOCK_USERS[17], "小心心", 100),
            (MOCK_USERS[6], "玫瑰", 20),
            (MOCK_USERS[2], "抖音1号", 1),
            (MOCK_USERS[10], "粉丝团灯牌", 1),
            (MOCK_USERS[4], "大啤酒", 4),
            (MOCK_USERS[12], "浪漫马车", 1),
            (MOCK_USERS[14], "爱的抱抱", 2),
            (MOCK_USERS[3], "人气票", 10),
            (MOCK_USERS[18], "飞机", 1),
            (MOCK_USERS[7], "加油鸭", 30),
        ]
        # 第二天换一换榜单
        if day_index == 1:
            gift_plan = [
                (MOCK_USERS[2], "嘉年华", 1),
                (MOCK_USERS[9], "豪华跑车", 3),
                (MOCK_USERS[17], "小心心", 100),
                (MOCK_USERS[19], "玫瑰", 50),
                (MOCK_USERS[0], "私人飞机", 1),
                (MOCK_USERS[6], "粉丝团灯牌", 1),
                (MOCK_USERS[15], "大啤酒", 8),
                (MOCK_USERS[3], "抖音1号", 1),
                (MOCK_USERS[10], "爱的抱抱", 3),
                (MOCK_USERS[1], "人气票", 15),
            ]
        elif day_index == 2:
            gift_plan = [
                (MOCK_USERS[6], "嘉年华", 2),
                (MOCK_USERS[0], "跑车", 1),
                (MOCK_USERS[2], "小心心", 100),
                (MOCK_USERS[9], "玫瑰", 10),
                (MOCK_USERS[18], "抖音1号", 1),
                (MOCK_USERS[12], "粉丝团灯牌", 1),
                (MOCK_USERS[4], "大啤酒", 2),
                (MOCK_USERS[15], "加油鸭", 20),
            ]

        gift_at = set(rng.sample(range(6, 35), len(gift_plan)))
        gift_iter = iter(gift_plan)
        for i in range(36):
            if i in gift_at:
                user, name, count = next(gift_iter)
                unit = dict(MOCK_GIFTS).get(name, 1)
                add(
                    "gift",
                    user,
                    content=name,
                    gift_name=name,
                    gift_count=count,
                    diamonds=unit * count,
                    visible=1 if unit * count > 90 else 0,
                )
            user = MOCK_USERS[rng.randrange(len(MOCK_USERS))]
            add("chat", user, content=rng.choice(MOCK_PHRASES))

        # 后半段再进几个人
        for user in rng.sample(MOCK_USERS, 4):
            add("entry", user, content="进入直播间")

    result.sort(key=lambda r: (r["received_at"], r["msg_id"]))
    return result


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------
def _weekday(date_text: str) -> str:
    try:
        return WEEKDAYS[datetime.strptime(date_text, "%Y-%m-%d").weekday()]
    except Exception:
        return ""


def _day_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    users = {r["user_id"] for r in rows if r["user_id"]}
    gifts = [r for r in rows if r["kind"] == "gift"]
    diamonds = sum(r["diamonds"] for r in gifts)
    payers = {r["user_id"] for r in gifts if r["user_id"]}
    return {
        "messages": len(rows),
        "chat": sum(1 for r in rows if r["kind"] == "chat"),
        "gift_events": len(gifts),
        "entry": sum(1 for r in rows if r["kind"] == "entry"),
        "users": len(users),
        "diamonds": diamonds,
        "payers": len(payers),
    }


def _leaderboard(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """当日全天礼物榜：按 user_id 汇总所有礼物（含低价）。"""
    board: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["kind"] != "gift" or not row["user_id"]:
            continue
        uid = row["user_id"]
        item = board.setdefault(
            uid,
            {
                "user_id": uid,
                "nickname": row["nickname"] or f"ID {row['user_id'][-8:]}",
                "diamonds": 0,
                "gift_count": 0,
                "events": 0,
                "last_time": "",
                "top_gift": "",
                "top_diamonds": -1,
            },
        )
        if row["nickname"]:
            item["nickname"] = row["nickname"]
        item["diamonds"] += row["diamonds"]
        item["gift_count"] += row["gift_count"]
        item["events"] += 1
        item["last_time"] = max(item["last_time"], row["received_at"][11:19])
        if row["diamonds"] > item["top_diamonds"]:
            item["top_diamonds"] = row["diamonds"]
            item["top_gift"] = row["gift_name"]
    return sorted(
        board.values(),
        key=lambda x: (x["diamonds"], x["gift_count"], x["user_id"]),
        reverse=True,
    )


def _group_days(rows: list[dict[str, Any]]) -> OrderedDict[str, list[dict[str, Any]]]:
    days: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        day = row["received_at"][:10]
        days.setdefault(day, []).append(row)
    return OrderedDict(sorted(days.items(), reverse=True))


def _json_for_script(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
CSS = r"""
:root {
  --bg: #080b11;
  --panel: #10151f;
  --panel2: #151b27;
  --line: #252d3c;
  --text: #e9eef7;
  --muted: #8d99ad;
  --accent: #ff2c55;
  --accent2: #ff6b87;
  --gold: #f5c451;
  --green: #2dd4a7;
  --blue: #6fb6ff;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background:
    radial-gradient(1000px 500px at 75% -10%, rgba(255,44,85,.14), transparent 60%),
    radial-gradient(800px 500px at 10% 0%, rgba(90,169,255,.08), transparent 55%),
    var(--bg);
  color: var(--text);
  font-family: "Microsoft YaHei UI", "PingFang SC", "Noto Sans SC", system-ui, sans-serif;
  line-height: 1.55;
}
a { color: inherit; text-decoration: none; }
.wrap { max-width: 1240px; margin: 0 auto; padding: 26px 22px 60px; }
.brandbar { display: flex; align-items: center; gap: 12px; color: var(--muted); font-size: 13px; }
.logo {
  width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center;
  background: linear-gradient(135deg, var(--accent), #ff8a5c);
  color: #fff; font-weight: 800; box-shadow: 0 8px 24px rgba(255,44,85,.25);
}
.hero { padding: 52px 0 26px; }
.kicker { color: var(--accent2); font-size: 13px; letter-spacing: .18em; text-transform: uppercase; }
h1 { margin: 10px 0 12px; font-size: clamp(34px, 5vw, 56px); line-height: 1.12; letter-spacing: -.02em; }
.hero p { margin: 0; color: var(--muted); font-size: 16px; max-width: 760px; }
.room-meta { margin-top: 10px; color: var(--muted); font-size: 13px; }
.room-meta b { color: var(--text); font-weight: 650; }
.hero .room-meta { max-width: 760px; }
.hero-actions { margin-top: 16px; display: flex; gap: 8px; flex-wrap: wrap; }
.demo-badge {
  display: inline-flex; align-items: center; gap: 7px; margin-left: 10px;
  padding: 4px 10px; border: 1px solid rgba(245,196,81,.35); border-radius: 999px;
  color: var(--gold); background: rgba(245,196,81,.08); font-size: 12px; vertical-align: middle;
}
.stats { display: grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap: 12px; margin: 28px 0 38px; }
.stat {
  background: linear-gradient(180deg, rgba(255,255,255,.035), rgba(255,255,255,.01)), var(--panel);
  border: 1px solid var(--line); border-radius: 16px; padding: 17px 18px;
}
.stat .label { color: var(--muted); font-size: 12px; }
.stat .value { font-size: 30px; font-weight: 750; margin-top: 4px; font-variant-numeric: tabular-nums; }
.stat .sub { color: var(--muted); font-size: 11px; margin-top: 3px; }
.section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin: 34px 0 14px; }
.section-head h2 { margin: 0; font-size: 20px; }
.section-head span { color: var(--muted); font-size: 12px; }
.month-title { color: var(--muted); font-size: 13px; font-weight: 600; letter-spacing: .08em; margin: 26px 0 10px; }
.date-grid { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 12px; }
.date-card {
  background: var(--panel); border: 1px solid var(--line); border-radius: 16px; padding: 18px;
  transition: transform .15s ease, border-color .15s ease, background .15s ease;
}
.date-card:hover { transform: translateY(-2px); border-color: rgba(255,44,85,.55); background: var(--panel2); }
.date-card .date { display: flex; align-items: baseline; gap: 8px; }
.date-card .day { font-size: 24px; font-weight: 750; font-variant-numeric: tabular-nums; }
.date-card .weekday { color: var(--muted); font-size: 12px; }
.date-card .nums { display: flex; gap: 16px; margin-top: 12px; color: var(--muted); font-size: 12px; }
.date-card .nums b { color: var(--text); font-size: 15px; font-weight: 650; }
.date-card .top { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }
.date-card .top b { color: var(--gold); }
.date-card .room-meta { margin-top: 10px; font-size: 12px; color: var(--muted); }
.daynav { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 26px 0 18px; }
.back { color: var(--muted); font-size: 13px; }
.back:hover { color: var(--text); }
.day-title { display: flex; align-items: baseline; flex-wrap: wrap; gap: 10px; margin: 6px 0 18px; }
.day-title h1 { font-size: clamp(30px, 4vw, 46px); margin: 0; }
.day-title .weekday { color: var(--muted); font-size: 15px; }
.day-meta { margin: -8px 0 18px; color: var(--muted); font-size: 13px; }
.day-meta b { color: var(--text); font-weight: 650; }
.layout { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 16px; align-items: start; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 16px; overflow: hidden; }
.card-head { padding: 15px 18px; border-bottom: 1px solid var(--line); display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.card-head h2 { margin: 0; font-size: 16px; }
.card-head .hint { color: var(--muted); font-size: 11px; }
.btn {
  appearance: none; border: 1px solid rgba(245,196,81,.35); border-radius: 8px;
  background: rgba(245,196,81,.08); color: var(--gold); cursor: pointer;
  padding: 7px 11px; font: inherit; font-size: 12px; font-weight: 650;
}
.btn:hover { background: rgba(245,196,81,.16); }
.btn.danger { color: #ff8fa3; border-color: rgba(255,93,115,.45); }
.btn.danger:hover { background: rgba(255,93,115,.14); }
.board-sum {
  display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  margin: 10px 14px 2px; padding: 8px 10px; border: 1px solid rgba(245,196,81,.22);
  border-radius: 8px; background: rgba(245,196,81,.05); color: var(--muted); font-size: 11.5px;
}
.board-sum b { color: var(--gold); font-size: 15px; font-variant-numeric: tabular-nums; }
.board-sum span { color: var(--muted); }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; padding: 14px 16px; border-bottom: 1px solid var(--line); }
.tabs { display: flex; gap: 6px; flex-wrap: wrap; }
.tab {
  border: 1px solid var(--line); background: transparent; color: var(--muted);
  border-radius: 999px; padding: 6px 12px; font: inherit; font-size: 12px; cursor: pointer;
}
.tab.active { background: var(--accent); border-color: var(--accent); color: #fff; }
.tab b { font-weight: 650; opacity: .8; margin-left: 3px; }
.search {
  flex: 1; min-width: 190px; background: var(--bg); border: 1px solid var(--line);
  color: var(--text); border-radius: 9px; padding: 7px 11px; font: inherit; font-size: 12px;
}
.search:focus { outline: none; border-color: rgba(255,44,85,.6); }
.table-wrap { overflow: auto; max-height: 70vh; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; table-layout: fixed; min-width: 820px; }
thead th:nth-child(1) { width: 58px; }
thead th:nth-child(2) { width: 54px; }
thead th:nth-child(3) { width: 122px; }
thead th:nth-child(4) { width: 96px; }
thead th:nth-child(5) { width: 52px; }
thead th:nth-child(6) { width: 42px; }
thead th:nth-child(8) { width: 52px; }
thead th:nth-child(9) { width: 62px; }
thead th {
  position: sticky; top: 0; z-index: 2; background: #0d121b; color: var(--muted);
  font-weight: 600; text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line);
  white-space: nowrap;
}
tbody td { padding: 9px 12px; border-bottom: 1px solid rgba(37,45,60,.62); vertical-align: top; }
tbody tr:hover { background: rgba(255,255,255,.025); }
.time { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.uid { font-family: Consolas, "Cascadia Mono", monospace; color: var(--blue); font-size: 11.5px; white-space: nowrap; }
.nick { color: var(--text); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.badge { display: inline-block; border-radius: 5px; padding: 1px 6px; font-size: 10.5px; border: 1px solid var(--line); color: var(--muted); white-space: nowrap; }
.badge.chat { color: #9cc8ff; border-color: rgba(111,182,255,.3); background: rgba(111,182,255,.07); }
.badge.gift { color: var(--gold); border-color: rgba(245,196,81,.3); background: rgba(245,196,81,.07); }
.badge.entry { color: var(--green); border-color: rgba(45,212,167,.3); background: rgba(45,212,167,.07); }
.lv { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.lv.hot { color: var(--accent2); font-weight: 650; }
.content { color: #d5deec; word-break: break-word; }
.giftline { color: var(--gold); font-weight: 650; }
.diamond { color: var(--gold); font-weight: 650; white-space: nowrap; font-variant-numeric: tabular-nums; }
.empty { padding: 32px 18px; text-align: center; color: var(--muted); font-size: 13px; }
.board { padding: 8px 0 4px; }
.board-row {
  display: grid; grid-template-columns: 26px minmax(0,1fr) auto; gap: 9px; align-items: start;
  padding: 10px 16px; border-bottom: 1px solid rgba(37,45,60,.62);
}
.board-row:last-child { border-bottom: 0; }
.rank { color: var(--muted); font-weight: 700; font-size: 12px; padding-top: 1px; }
.rank.top1 { color: var(--gold); }
.rank.top2 { color: #c9d6e8; }
.rank.top3 { color: #d69b72; }
.board-main { min-width: 0; }
.board-name { font-size: 12.5px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.board-id { color: var(--muted); font-family: Consolas, monospace; font-size: 10.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.board-gift { color: var(--muted); font-size: 10.5px; margin-top: 2px; }
.board-val { text-align: right; }
.board-diamond { color: var(--gold); font-weight: 750; font-size: 13px; font-variant-numeric: tabular-nums; }
.board-count { color: var(--muted); font-size: 10px; }
.legend { color: var(--muted); font-size: 11px; padding: 11px 16px; border-top: 1px solid var(--line); }
.foot { color: var(--muted); font-size: 11px; margin-top: 32px; line-height: 1.8; }
.foot b { color: #b9c6d8; font-weight: 600; }
@media (max-width: 980px) {
  .stats { grid-template-columns: repeat(2, minmax(0,1fr)); }
  .date-grid { grid-template-columns: repeat(2, minmax(0,1fr)); }
  .layout { grid-template-columns: 1fr; }
  .board { max-height: none; }
}
@media (max-width: 560px) {
  .wrap { padding: 18px 12px 40px; }
  .stats { grid-template-columns: 1fr 1fr; }
  .date-grid { grid-template-columns: 1fr; }
  .hero { padding-top: 30px; }
}
"""

INDEX_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<div class="wrap">
  <div class="brandbar"><span class="logo">弹</span><span>抖音弹幕归档</span></div>
  <div class="hero">
    <div class="kicker">Douyin Live Archive</div>
    <h1>直播弹幕与礼物<br>按天归档</h1>
    <p>所有弹幕、礼物、入场按时间戳保存；每一天一个页面，附当日全天礼物榜。__BADGE__</p>
    <div class="hero-actions"><button class="btn" id="export-all">导出全部 CSV</button></div>
  </div>
  <div class="stats">
    <div class="stat"><div class="label">收录天数</div><div class="value">__DAYS__</div><div class="sub">按自然日分类</div></div>
    <div class="stat"><div class="label">消息总数</div><div class="value">__MESSAGES__</div><div class="sub">弹幕 + 礼物 + 入场</div></div>
    <div class="stat"><div class="label">礼物总额</div><div class="value">__DIAMONDS__</div><div class="sub">单位：钻</div></div>
    <div class="stat"><div class="label">独立用户</div><div class="value">__USERS__</div><div class="sub">按用户 ID 去重</div></div>
    <div class="stat"><div class="label">付费用户</div><div class="value">__PAYERS__</div><div class="sub">有礼物记录的用户</div></div>
  </div>
  <div class="section-head"><h2>按日期查看</h2><span>点击日期进入当天详情</span></div>
  __DATE_SECTIONS__
  <div class="foot">
    <b>字段说明</b>：消息按采集时间排序；“当日礼物榜”按用户 ID 汇总当天全部礼物（包含未达到 90 钻展示门槛的礼物）。<br>
    <b>数据来源</b>：本地弹幕姬日志。__FOOTNOTE__
  </div>
</div>
<script>
const ALL_DATA = __ALL_DATA__;
function csvCell(value) {
  const s = String(value == null ? "" : value);
  return (s.includes('"') || s.includes(",") || s.includes(String.fromCharCode(10))) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
function downloadAllCsv() {
  const cols = ["received_at","kind","user_id","nickname","fans_club_level","pay_level","content","gift_name","gift_count","diamonds"];
  const lines = [cols.join(",")];
  for (const r of ALL_DATA) lines.push(cols.map(c => csvCell(r[c])).join(","));
  const blob = new Blob([String.fromCharCode(0xFEFF) + lines.join(String.fromCharCode(10))], {type: "text/csv;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "弹幕归档_全部.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}
document.getElementById("export-all").addEventListener("click", downloadAllCsv);
</script>
</body>
</html>
"""

DAY_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__DATE__ · __TITLE__</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<div class="wrap">
  <div class="brandbar"><span class="logo">弹</span><a href="index.html">抖音弹幕归档</a></div>
  <div class="daynav"><a class="back" href="index.html">← 返回日期列表</a><span class="badge">__BADGE_TEXT__</span></div>
  <div class="day-title"><h1>__DATE__</h1><span class="weekday">__WEEKDAY__ · 直播记录</span></div>
  <div class="day-meta">__ROOM_META__</div>
  <div class="stats">
    <div class="stat"><div class="label">消息总数</div><div class="value">__MESSAGES__</div><div class="sub">弹幕 __CHAT__ · 入场 __ENTRY__</div></div>
    <div class="stat"><div class="label">礼物事件</div><div class="value">__GIFTS__</div><div class="sub">连击已按整组合并</div></div>
    <div class="stat"><div class="label">礼物总额</div><div class="value">__DIAMONDS__</div><div class="sub">单位：钻</div></div>
    <div class="stat"><div class="label">独立用户</div><div class="value">__USERS__</div><div class="sub">按用户 ID 去重</div></div>
    <div class="stat"><div class="label">付费人数</div><div class="value">__PAYERS__</div><div class="sub">当天送过礼物</div></div>
  </div>

  <div class="layout">
    <div class="card">
      <div class="card-head"><h2>全部消息</h2><span class="hint">按时间升序 · 支持搜索</span></div>
      <div class="toolbar">
        <div class="tabs">
          <button class="tab active" data-kind="all">全部 <b>__MESSAGES__</b></button>
          <button class="tab" data-kind="chat">弹幕 <b>__CHAT__</b></button>
          <button class="tab" data-kind="gift">礼物 <b>__GIFTS__</b></button>
          <button class="tab" data-kind="entry">入场 <b>__ENTRY__</b></button>
        </div>
        <input id="search" class="search" placeholder="搜索昵称 / 用户 ID / 内容 / 礼物…">
        <button class="btn" id="export-day">导出当日 CSV</button>
        <button class="btn danger" id="delete-day">删除当日</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>类型</th><th>用户 ID</th><th>昵称</th><th>粉丝团</th><th>消费</th><th>内容 / 礼物</th><th>数量</th><th>钻石</th></tr></thead>
          <tbody id="rows"></tbody>
        </table>
        <div id="empty" class="empty" style="display:none">没有匹配的消息</div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>当日礼物榜</h2><span class="hint">按用户 ID 汇总</span></div>
      <div class="board-sum">当日总额 <b>__DIAMONDS__ 钻</b><span>· __PAYERS__ 人付费 · 全部场次汇总</span></div>
      <div id="board" class="board"></div>
      <div class="legend">统计当天全部礼物，包括 1 钻小礼物；连击按整组合并后的总数量。</div>
    </div>
  </div>

  <div class="foot">
    <b>说明</b>：时间取自采集端本地时间；礼物“钻石”为服务端下发的单价 × 数量。<br>
    <b>提示</b>：搜索支持用户 ID、昵称、弹幕内容和礼物名；礼物榜展示前 30 名。
  </div>
</div>
<script>
const DATA = __DATA__;
const BOARD = __BOARD__;
const BADGE = "__BADGE_TEXT__";
let activeKind = "all";
let query = "";

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}
function fmt(n) {
  return Number(n || 0).toLocaleString("zh-CN");
}
function typeBadge(kind) {
  const label = kind === "chat" ? "弹幕" : kind === "gift" ? "礼物" : "入场";
  return `<span class="badge ${kind}">${label}</span>`;
}
function contentCell(r) {
  if (r.kind === "gift") {
    return `<span class="giftline">${esc(r.gift_name || r.content)}</span>`;
  }
  if (r.kind === "entry") return `<span style="color:var(--muted)">进入直播间</span>`;
  return esc(r.content);
}
function lvCell(n, hot) {
  if (!n) return `<span class="lv">-</span>`;
  return `<span class="lv ${hot ? "hot" : ""}">Lv.${n}</span>`;
}
function renderRows() {
  const q = query.trim().toLowerCase();
  const rows = DATA.filter(r => {
    if (activeKind !== "all" && r.kind !== activeKind) return false;
    if (!q) return true;
    return [r.nickname, r.user_id, r.content, r.gift_name].join(" ").toLowerCase().includes(q);
  });
  const tbody = document.getElementById("rows");
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td class="time">${esc(r.received_at.slice(11, 19))}</td>
      <td>${typeBadge(r.kind)}</td>
      <td class="uid">${esc(r.user_id || "-")}</td>
      <td class="nick">${esc(r.nickname || "-")}</td>
      <td>${lvCell(r.fans_club_level, r.fans_club_level >= 15)}</td>
      <td class="lv">${r.pay_level ? "Lv." + r.pay_level : "-"}</td>
      <td class="content">${contentCell(r)}</td>
      <td class="lv">${r.kind === "gift" ? "× " + fmt(r.gift_count) : ""}</td>
      <td class="diamond">${r.kind === "gift" ? fmt(r.diamonds) : ""}</td>
    </tr>`).join("");
  document.getElementById("empty").style.display = rows.length ? "none" : "block";
}
function renderBoard() {
  const box = document.getElementById("board");
  if (!BOARD.length) {
    box.innerHTML = `<div class="empty">当天还没有礼物记录</div>`;
    return;
  }
  box.innerHTML = BOARD.slice(0, 30).map((u, i) => {
    const rankClass = i === 0 ? "top1" : i === 1 ? "top2" : i === 2 ? "top3" : "";
    return `<div class="board-row">
      <div class="rank ${rankClass}">${i + 1}</div>
      <div class="board-main">
        <div class="board-name">${esc(u.nickname)}</div>
        <div class="board-id">ID ${esc(u.user_id)}</div>
        <div class="board-gift">${esc(u.top_gift || "-")} · ${u.events} 次 · 最后 ${esc(u.last_time || "-")}</div>
      </div>
      <div class="board-val">
        <div class="board-diamond">${fmt(u.diamonds)} 钻</div>
        <div class="board-count">${fmt(u.gift_count)} 个</div>
      </div>
    </div>`;
  }).join("");
}
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    activeKind = btn.dataset.kind;
    renderRows();
  });
});
document.getElementById("search").addEventListener("input", e => {
  query = e.target.value;
  renderRows();
});
renderRows();
renderBoard();

function csvCell(value) {
  const s = String(value == null ? "" : value);
  return (s.includes('"') || s.includes(",") || s.includes(String.fromCharCode(10))) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
function downloadDayCsv() {
  const cols = ["received_at","kind","user_id","nickname","fans_club_level","pay_level","content","gift_name","gift_count","diamonds"];
  const lines = [cols.join(",")];
  for (const r of DATA) lines.push(cols.map(c => csvCell(r[c])).join(","));
  const blob = new Blob([String.fromCharCode(0xFEFF) + lines.join(String.fromCharCode(10))], {type: "text/csv;charset=utf-8"});
  const a = document.createElement("a");
  const day = (DATA[0] && DATA[0].received_at ? DATA[0].received_at.slice(0, 10) : "day");
  a.href = URL.createObjectURL(blob);
  a.download = "弹幕归档_" + day + ".csv";
  a.click();
  URL.revokeObjectURL(a.href);
}
document.getElementById("export-day").addEventListener("click", downloadDayCsv);
async function deleteDay() {
  const day = "__DATE__";
  if (!confirm("确定从归档中删除 " + day + " 的记录吗？原始日志会保留。" )) return;
  try {
    const resp = await fetch("/api/delete-day", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({day: day})
    });
    const result = await resp.json().catch(() => ({}));
    if (!resp.ok || !result.ok) throw new Error(result.error || "删除失败");
    location.href = "index.html";
  } catch (err) {
    alert("删除失败：" + err.message + String.fromCharCode(10) + String.fromCharCode(10) + "请从弹幕助手打开归档网站后再删除。" );
  }
}
document.getElementById("delete-day").addEventListener("click", deleteDay);
</script>
</body>
</html>
"""


def _render_index(
    title: str,
    days: OrderedDict[str, list[dict[str, Any]]],
    mock: bool,
    meta: dict[str, Any] | None = None,
    live_id: str | None = None,
) -> str:
    total_rows = [r for rows in days.values() for r in rows]
    total_stats = _day_stats(total_rows)
    badge = '<span class="demo-badge">● 模拟数据</span>' if mock else ""
    months: OrderedDict[str, list[str]] = OrderedDict()
    for day in days.keys():
        month = day[:7]
        months.setdefault(month, []).append(day)

    sections: list[str] = []
    meta_line = _room_meta_html(meta or {}, live_id)
    for month, dates in months.items():
        cards = []
        for day in dates:
            rows = days[day]
            stats = _day_stats(rows)
            board = _leaderboard(rows)
            top = board[0] if board else None
            top_text = (
                f"榜一 <b>{html.escape(str(top['nickname']))}</b> · {top['diamonds']:,} 钻"
                if top
                else "暂无礼物"
            )
            cards.append(
                f"""<a class="date-card" href="{day}.html">
  <div class="date"><span class="day">{day[5:]}</span><span class="weekday">{_weekday(day)}</span></div>
  <div class="room-meta">{meta_line}</div>
  <div class="nums"><span><b>{stats['messages']}</b> 条</span><span><b>{stats['users']}</b> 人</span><span><b>{stats['diamonds']:,}</b> 钻</span></div>
  <div class="top">{top_text}</div>
</a>"""
            )
        sections.append(
            f'<div class="month-title">{month.replace("-", " 年 ")} 月</div>'
            f'<div class="date-grid">{"".join(cards)}</div>'
        )

    page = INDEX_TEMPLATE
    replacements = {
        "__TITLE__": html.escape(title),
        "__BADGE__": badge,
        "__DAYS__": str(len(days)),
        "__MESSAGES__": f"{total_stats['messages']:,}",
        "__DIAMONDS__": f"{total_stats['diamonds']:,}",
        "__USERS__": f"{total_stats['users']:,}",
        "__PAYERS__": f"{total_stats['payers']:,}",
        "__DATE_SECTIONS__": "".join(sections) or '<div class="empty">还没有数据</div>',
        "__FOOTNOTE__": "当前为模拟数据，用于确认展示结构；接入真实日志后会自动按天生成。" if mock else "由弹幕姬本地日志生成。",
        "__ROOM_META__": _room_meta_html(meta or {}, live_id),
        "__ALL_DATA__": _json_for_script(total_rows),
    }
    for key, value in replacements.items():
        page = page.replace(key, value)
    return page


def _render_day(
    title: str,
    day: str,
    rows: list[dict[str, Any]],
    mock: bool,
    meta: dict[str, Any] | None = None,
    live_id: str | None = None,
) -> str:
    stats = _day_stats(rows)
    board = _leaderboard(rows)
    badge_text = "模拟数据" if mock else "真实记录"
    page = DAY_TEMPLATE
    replacements = {
        "__TITLE__": html.escape(title),
        "__DATE__": day,
        "__WEEKDAY__": _weekday(day),
        "__BADGE_TEXT__": badge_text,
        "__MESSAGES__": f"{stats['messages']:,}",
        "__CHAT__": f"{stats['chat']:,}",
        "__GIFTS__": f"{stats['gift_events']:,}",
        "__ENTRY__": f"{stats['entry']:,}",
        "__DIAMONDS__": f"{stats['diamonds']:,}",
        "__USERS__": f"{stats['users']:,}",
        "__PAYERS__": f"{stats['payers']:,}",
        "__DATA__": _json_for_script(rows),
        "__BOARD__": _json_for_script(board),
        "__ROOM_META__": _room_meta_html(meta or {}, live_id),
    }
    for key, value in replacements.items():
        page = page.replace(key, value)
    return page


def build_site(
    rows: list[dict[str, Any]],
    out_dir: str | Path,
    title: str = "抖音弹幕归档",
    mock: bool = False,
    meta: dict[str, Any] | None = None,
    live_id: str | None = None,
) -> Path:
    out = Path(out_dir)
    (out / "assets").mkdir(parents=True, exist_ok=True)
    (out / "assets" / "style.css").write_text(CSS, encoding="utf-8")
    days = _group_days(rows)
    (out / "index.html").write_text(
        _render_index(title, days, mock, meta=meta, live_id=live_id), encoding="utf-8"
    )
    for day, day_rows in days.items():
        (out / f"{day}.html").write_text(
            _render_day(title, day, day_rows, mock, meta=meta, live_id=live_id),
            encoding="utf-8",
        )
    for stale in out.glob("????-??-??.html"):
        if stale.stem not in days:
            try:
                stale.unlink()
            except OSError:
                pass
    manifest = {
        "title": title,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "live_id": (meta or {}).get("live_id") or live_id,
        "anchor_name": (meta or {}).get("anchor_name", ""),
        "room_id": (meta or {}).get("room_id", ""),
        "days": [
            {"date": day, "messages": len(day_rows), **_day_stats(day_rows)}
            for day, day_rows in days.items()
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="导出弹幕姬静态网站")
    parser.add_argument("--logs", default="logs", help="日志目录")
    parser.add_argument("--out", default="site", help="输出目录")
    parser.add_argument("--title", default="抖音弹幕归档")
    parser.add_argument("--mock", action="store_true", help="生成模拟数据")
    parser.add_argument("--live-id", default=None, help="只导出指定直播间，例如 617650815017")
    args = parser.parse_args()

    if args.mock:
        rows = mock_messages()
        mock = True
        meta = {"live_id": "617650815017", "anchor_name": "小绿喵喵"}
    else:
        rows = load_messages_from_logs(args.logs, live_id=args.live_id)
        mock = False
        meta = load_room_meta(args.logs, args.live_id)

    out = build_site(
        rows, args.out, title=args.title, mock=mock, meta=meta, live_id=args.live_id
    )
    print(f"OK {len(rows)} messages -> {out.resolve()}")
    print((out / "index.html").resolve())


if __name__ == "__main__":
    main()
