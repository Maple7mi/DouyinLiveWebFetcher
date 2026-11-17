# -*- coding: utf-8 -*-
"""One-off preview: Aurora Dark with entry + gift merged into the main feed."""
from __future__ import annotations

import html
from pathlib import Path

import build

OUT = Path(__file__).resolve().parent

FEED = [
    ("chat", 42, 21, "一只小耳朵", "这个皮肤也太好看了吧", "super"),
    ("entry", None, 18, "小鹿乱撞", "进入直播间", ""),
    ("chat", 51, 24, "超级大哥", "嘉年华安排上", "super"),
    ("chat", 36, 15, "北岛", "刚刚那波操作太秀了", "hot"),
    ("entry", None, 21, "一只小耳朵", "进入直播间", ""),
    ("chat", 18, 10, "奶茶不加糖", "主播今天状态好好", ""),
    ("entry", None, 16, "星星点灯", "进入直播间", ""),
    ("chat", 27, 16, "小鹿乱撞", "灯牌已点亮", "hot"),
    ("chat", 9, 6, "晚风", "哈哈哈哈哈哈哈", ""),
    ("entry", None, 22, "夜航船", "进入直播间", ""),
]

GIFT_CARDS = [
    ("8473620195", "嘉年华", 2, "60,000"),
    ("5382019476", "跑车", 1, "1,200"),
    ("1839201746", "粉丝团灯牌", 100, "100"),
    ("7744102938", "抖音1号", 1, "10,001"),
    ("6612083745", "直升机", 1, "2,999"),
]

def feed_html() -> str:
    out = []
    for kind, pay, fan, nick, content, cls in FEED:
        if kind == "entry":
            out.append(
                '<div class="chat-row feed-entry">'
                '<span class="lv">—</span>'
                f'<span class="lv feed-entry-lv">Lv.{fan}</span>'
                f'<span class="nick">{html.escape(nick)}</span>'
                f'<span class="content"><span class="row-tag">入场</span>'
                f'<span class="feed-text">{html.escape(content)}</span></span>'
                "</div>"
            )
        else:
            cls_attr = f" {cls}" if cls else ""
            out.append(
                f'<div class="chat-row{cls_attr}">'
                f'<span class="lv">Lv.{pay}</span>'
                f'<span class="lv">Lv.{fan}</span>'
                f'<span class="nick">{html.escape(nick)}</span>'
                f'<span class="content"><span class="feed-text">{html.escape(content)}</span></span>'
                "</div>"
            )
    return "".join(out)


def gift_cards_html() -> str:
    out = []
    for idx, (uid, name, count, value) in enumerate(GIFT_CARDS, 1):
        out.append(
            '<div class="gift-item">'
            f'<span class="gift-rank">{idx:02d}</span>'
            f'<span class="gift-main"><span class="gift-name">{html.escape(name)}</span>'
            f'<span class="gift-count">×{count}</span></span>'
            f'<span class="gift-id">ID {uid}</span>'
            f'<span class="gift-value">{value} <small>钻</small></span>'
            "</div>"
        )
    return "".join(out)


EXTRA_CSS = r"""
body.theme-aurora{--entry-bg:rgba(0,229,255,.09);--entry-text:#bffaff;--entry-bar:#00e5ff;--gift:#ff2d95}
.layout-solo .dashboard{grid-template-columns:minmax(0,1fr);grid-template-rows:104px minmax(0,1fr);grid-template-areas:"summary" "chat"}
.layout-solo .chat{grid-area:chat}
.merged-chat .panel-head{padding-bottom:8px}
.feed-note{font-size:9px;line-height:1.5;color:var(--muted);margin-top:5px}
.feed-note b{color:var(--entry-text);font-weight:600}
.feed-note i{font-style:normal;color:var(--gift);font-weight:600}
.merged-chat .content{display:flex;align-items:center;gap:7px;min-width:0}
.feed-text{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.feed-entry{background:var(--entry-bg);border-left:3px solid var(--entry-bar);padding-left:14px;color:var(--entry-text)}
.feed-entry .lv,.feed-entry .nick,.feed-entry .content{color:inherit}
.feed-entry .feed-entry-lv{color:var(--entry-bar);font-weight:600}
.feed-entry .content{font-weight:500}
.row-tag{display:inline-flex;align-items:center;height:17px;padding:0 6px;margin-right:7px;background:var(--entry-bar);color:#041014;border-radius:3px;font-family:var(--font);font-size:8px;font-weight:800;letter-spacing:.6px;vertical-align:1px}
.gift-strip{flex:none;border-top:1px solid var(--border);background:linear-gradient(180deg,rgba(255,45,149,.045),rgba(255,45,149,.015));padding:10px 17px 12px}
.gift-strip-head{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:7px}
.gift-strip-title{display:flex;align-items:baseline;gap:9px}
.gift-strip-title strong{font-size:13px;letter-spacing:.2px;color:#fff}
.gift-strip-title span{font-size:8px;letter-spacing:1.5px;color:var(--gift);font-weight:800}
.gift-strip-meta{font-size:9px;color:var(--muted)}
.gift-list{display:flex;flex-direction:column;gap:4px}
.gift-item{display:grid;grid-template-columns:30px minmax(0,1fr) 150px 112px;gap:12px;align-items:center;min-height:34px;padding:4px 10px;border:1px solid rgba(255,45,149,.34);border-radius:6px;background:linear-gradient(90deg,rgba(255,45,149,.15),rgba(255,45,149,.045));box-shadow:inset 0 0 14px rgba(255,45,149,.03)}
.gift-rank{font-family:var(--mono);font-size:11px;color:#ff9bc8}
.gift-main{display:flex;align-items:baseline;gap:8px;min-width:0}
.gift-name{font-size:15px;font-weight:700;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gift-count{font-size:12px;color:#ffc0da;white-space:nowrap}
.gift-id{font-family:var(--mono);font-size:10px;color:#d8a6bf;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gift-value{font-family:var(--number-font);font-size:18px;font-weight:700;color:var(--gold);text-align:right;white-space:nowrap}
.gift-value small{font-family:var(--font);font-size:9px;font-weight:400;color:#c89bad}
"""


def page() -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1600, initial-scale=1">
<title>Aurora Dark · 入场与礼物合并预览</title>
<style>{build.CSS}{EXTRA_CSS}</style>
</head>
<body class="theme-aurora layout-solo">
<div class="app">
  <header class="topbar">
    <div class="brand"><div class="logo">弹</div><div><h1>弹幕助手</h1><p>抖音直播 · 弹幕 / 礼物 / 数据</p></div></div>
    <div class="room-pill"><span class="live-tag">LIVE</span><span class="room-name">小缘喵喵</span><span class="room-id">943973166532</span></div>
    <div class="top-actions"><button class="btn ghost">导出网站</button><button class="btn primary"><span class="status-dot"></span>已连接</button></div>
  </header>

  <div class="dashboard">
    <div class="summary">{build.metric_html()}</div>

    <section class="panel chat merged-chat">
      <div class="panel-head">
        <div>
          <div class="eyebrow">LIVE FEED</div>
          <h2>弹幕 <span class="subtle">聊天 / 入场 / 礼物</span></h2>
          <div class="feed-note"><b>入场 ≥15</b> 青色高亮 · <i>礼物 &gt;90 钻</i> 洋红最高级高亮 · 弹幕 35+/15+ 金色、40+/18+ 红色</div>
        </div>
        <div class="head-right"><span class="badge">10</span></div>
      </div>
      <div class="chat-head"><span>消费</span><span>粉丝团</span><span>昵称</span><span>内容</span></div>
      <div class="chat-list">{feed_html()}</div>

      <div class="gift-strip">
        <div class="gift-strip-head">
          <div class="gift-strip-title"><span>GIFT HIGHLIGHT</span><strong>礼物 · 最近 5 条</strong></div>
          <span class="gift-strip-meta">仅显示整组累计 &gt; 90 钻 · 最高级高亮</span>
        </div>
        <div class="gift-list">{gift_cards_html()}</div>
      </div>
    </section>
  </div>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    out = OUT / "11-aurora-merged.html"
    out.write_text(page(), encoding="utf-8")
    print(out)
