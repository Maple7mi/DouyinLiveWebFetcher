#!/usr/bin/env python
# coding: utf-8
"""抖音直播弹幕采集：实时打印，同时把聊天消息写入 CSV。

用法：
    python collect.py "https://live.douyin.com/123456789"
    python collect.py 123456789
"""

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path

import requests

from liveMan import DouyinLiveWebFetcher
from protobuf.douyin import ChatMessage

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
)


def parse_live_id(value: str) -> str:
    """从直播间链接、分享短链或纯房间号中取出 live_id。"""
    value = value.strip()
    match = re.search(r"live\.douyin\.com/(\d+)", value)
    if match:
        return match.group(1)
    if value.isdigit():
        return value

    # 抖音 App 的分享链接通常是 v.douyin.com 短链，跟随跳转后再取房间号
    url_match = re.search(r"https?://[^\s]+", value)
    if url_match and "v.douyin.com" in url_match.group(0):
        try:
            response = requests.get(
                url_match.group(0),
                headers={"User-Agent": USER_AGENT},
                timeout=15,
                allow_redirects=True,
            )
        except Exception as exc:
            raise SystemExit(f"短链接解析失败：{exc}") from exc
        match = re.search(r"live\.douyin\.com/(\d+)", f"{response.url}\n{response.text}")
        if match:
            return match.group(1)

    raise SystemExit(f"无法识别直播间链接或房间号：{value!r}")


class CsvCollector(DouyinLiveWebFetcher):
    """在原始弹幕姬的基础上，补充粉丝牌等级并落盘 CSV。"""

    CSV_HEADER = [
        "received_at",
        "msg_id",
        "user_id",
        "nickname",
        "fans_club_name",
        "fans_club_level",
        "pay_level",
        "content",
    ]

    def __init__(self, live_id: str, csv_path: Path):
        super().__init__(live_id)
        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.csv_path.exists()
        self._csv_file = self.csv_path.open("a", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._csv_file)
        if is_new:
            self._writer.writerow(self.CSV_HEADER)
            self._csv_file.flush()
        self._seen_msg_ids = set()

    def close_csv(self):
        try:
            self._csv_file.close()
        except Exception:
            pass

    def _parseChatMsg(self, payload):
        """聊天消息：发送人 ID、昵称、粉丝牌等级、内容。"""
        message = ChatMessage().parse(payload)
        user = message.user
        msg_id = str(message.common.msg_id or "")
        if msg_id:
            if msg_id in self._seen_msg_ids:
                return
            self._seen_msg_ids.add(msg_id)

        fans_club = user.fans_club.data if user.fans_club else None
        club_name = (fans_club.club_name or "").strip() if fans_club else ""
        club_level = fans_club.level if fans_club else 0
        pay_level = user.pay_grade.level if user.pay_grade else 0
        badge = f"{club_name} Lv.{club_level}" if (club_name or club_level) else "-"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print(
            f"【聊天】{now} | ID:{user.id} | {user.nick_name} | "
            f"粉丝牌:{badge} | 财富:{pay_level} | {message.content}",
            flush=True,
        )
        self._writer.writerow(
            [
                now,
                msg_id,
                user.id,
                user.nick_name,
                club_name,
                club_level,
                pay_level,
                message.content,
            ]
        )
        self._csv_file.flush()

    def _parseRankMsg(self, payload):
        # 原始实现会把整个榜单对象打印出来，刷屏且没有用处
        print("【排行榜】收到排行榜数据（已省略）", flush=True)


def main():
    parser = argparse.ArgumentParser(description="抖音直播弹幕采集（CSV 落盘）")
    parser.add_argument("room", help="直播间链接或房间号")
    parser.add_argument("--out", help="CSV 输出路径（默认 logs/chat_<房间号>_<时间>.csv）")
    args = parser.parse_args()

    live_id = parse_live_id(args.room)
    if args.out:
        out = Path(args.out)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path("logs") / f"chat_{live_id}_{stamp}.csv"

    room = CsvCollector(live_id, out)
    print(f"直播间 live_id：{live_id}")
    print(f"CSV 输出：{out.resolve()}")
    try:
        room.start()
    except KeyboardInterrupt:
        print("\n已停止采集。")
    finally:
        room.close_csv()


if __name__ == "__main__":
    main()
