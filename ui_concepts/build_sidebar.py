# -*- coding: utf-8 -*-
"""Preview: Aurora Dark, 70% chat + 30% right status/gift sidebar."""
from __future__ import annotations

import html
from pathlib import Path

OUT = Path(__file__).resolve().parent

FEED = [
    ("chat", 42, 21, "一只小耳朵", "这个皮肤也太好看了吧", "super"),
    ("entry", None, 18, "小鹿乱撞", "进入直播间", ""),
    ("chat", 51, 24, "超级大哥", "嘉年华安排上", "super"),
    ("chat", 36, 15, "北岛", "刚刚那波操作太秀了", "hot"),
    ("entry", None, 21, "一只小耳朵", "进入直播间", ""),
    ("chat", 18, 10, "奶茶不加糖", "主播今天状态好好，这波操作真的太细了，感觉已经稳了，结果最后被翻盘了，真的太可惜了", ""),
    ("entry", None, 16, "星星点灯", "进入直播间", ""),
    ("chat", 27, 16, "小鹿乱撞", "灯牌已点亮", "hot"),
    ("chat", 9, 6, "晚风", "哈哈哈哈哈哈哈", ""),
    ("entry", None, 22, "夜航船", "进入直播间", ""),
    ("chat", 16, 9, "橘子汽水", "今天 BGM 好好听", ""),
    ("chat", 31, 12, "小尾巴", "主播别下播啊", ""),
    ("chat", 45, 20, "夜航船", "再打一局", "super"),
    ("chat", 7, 4, "新来的", "关注了", ""),
]

GIFTS = [
    ("29", "14", "小洋>.<", "星光闪耀", 12, "108"),
    ("29", "14", "小洋>.<", "星光闪耀", 11, "99"),
    ("16", "16", "小鹿乱撞", "跑车", 1, "1,200"),
    ("51", "24", "超级大哥", "嘉年华", 2, "60,000"),
    ("22", "19", "月亮邮差", "粉丝团灯牌", 100, "100"),
]

CSS = r"""
:root{
  --bg:#070a10; --panel:#11151f; --panel2:#0d121b; --line:#222a3a;
  --fg:#eef2ff; --muted:#7f8ba3; --accent:#7c5cff; --cyan:#00e5ff;
  --gold:#f5c451; --pink:#ff2d95; --red:#ff5a72;
  --font:"Microsoft YaHei UI","Microsoft YaHei","PingFang SC",sans-serif;
  --mono:"Cascadia Mono","Consolas",monospace;
  --num:"Bahnschrift","DIN Alternate","Arial Narrow",sans-serif;
}
*{box-sizing:border-box}
html,body{margin:0;width:100%;height:100%;background:var(--bg);color:var(--fg);font-family:var(--font)}
body{
  background:
    radial-gradient(900px 500px at 12% -8%,rgba(124,92,255,.16),transparent 62%),
    radial-gradient(800px 500px at 94% 2%,rgba(0,229,255,.07),transparent 60%),
    var(--bg);
}
.app{
  height:1000px;padding:18px;display:grid;grid-template-columns:minmax(0,7fr) minmax(0,3fr);
  gap:14px;
}
.panel{background:linear-gradient(180deg,rgba(18,24,36,.96),rgba(13,18,27,.98));border:1px solid var(--line);border-radius:14px;box-shadow:0 18px 50px rgba(0,0,0,.25)}
.chat-panel{display:flex;flex-direction:column;min-width:0;overflow:hidden}
.chat-head{display:flex;justify-content:space-between;align-items:flex-start;padding:18px 20px 12px;border-bottom:1px solid var(--line)}
.eyebrow{font-size:12px;letter-spacing:2.1px;color:var(--accent);font-weight:800;margin-bottom:6px}
h1{margin:0;font-size:24px;letter-spacing:.3px}
h1 span{font-size:12px;color:var(--muted);font-weight:400;margin-left:9px;letter-spacing:.5px}
.badge{min-width:30px;height:25px;padding:0 9px;border-radius:5px;background:rgba(124,92,255,.14);border:1px solid rgba(124,92,255,.26);color:#cfc6ff;font-family:var(--mono);font-size:14px;display:flex;align-items:center;justify-content:center}
.legend{display:flex;gap:20px;padding:10px 20px;border-bottom:1px solid var(--line);font-size:13px;color:var(--muted)}
.legend b{font-weight:500}
.legend .g{color:var(--gold)} .legend .r{color:#ff8d9b} .legend .c{color:var(--cyan)} .legend .p{color:var(--pink)}
.chat-table{flex:1;min-height:0;display:flex;flex-direction:column;padding:0 12px;overflow:hidden}
.chat-row{display:grid;grid-template-columns:96px 80px 210px minmax(0,1fr);align-items:center;min-height:56px;padding:8px 11px;border-bottom:1px solid rgba(34,42,58,.62);font-size:18px}
.chat-headrow{min-height:42px;padding-top:0;padding-bottom:0;color:#7d8aa1;font-size:14px;letter-spacing:.4px;border-bottom:1px solid var(--line);background:transparent}
.chat-row .lv{font-family:var(--mono);font-size:16px;color:#9aa7bd}
.chat-row .nick{font-weight:700;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:18px}
.chat-row .content{min-width:0;white-space:normal;overflow:hidden;color:#dfe5f4;line-height:1.32;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;word-break:break-word}
.chat-row.hot{background:rgba(245,196,81,.12);border-left:3px solid var(--gold);padding-left:7px}
.chat-row.hot .lv,.chat-row.hot .nick,.chat-row.hot .content{color:#ffe5a6}
.chat-row.super{background:rgba(255,45,149,.16);border-left:3px solid var(--pink);padding-left:7px}
.chat-row.super .lv,.chat-row.super .nick,.chat-row.super .content{color:#ffd7e5}
.chat-row.entry{background:rgba(0,229,255,.08);border-left:3px solid var(--cyan);padding-left:7px}
.chat-row.entry .lv,.chat-row.entry .nick,.chat-row.entry .content{color:#bdf8ff}
.entry-tag{display:inline-flex;height:18px;align-items:center;padding:0 6px;margin-right:7px;background:var(--cyan);color:#031017;border-radius:3px;font-size:9px;font-weight:800;letter-spacing:.5px}
.chat-foot{padding:9px 20px;border-top:1px solid var(--line);display:flex;justify-content:space-between;font-size:9px;color:#657187}
.sidebar{display:flex;flex-direction:column;min-width:0;gap:10px;overflow:hidden}
.side-card{background:linear-gradient(180deg,rgba(18,24,36,.96),rgba(13,18,27,.98));border:1px solid var(--line);border-radius:12px}
.brand{display:flex;gap:11px;align-items:center;padding:13px 14px 12px}
.logo{width:36px;height:36px;border-radius:9px;background:linear-gradient(145deg,#9c5cff,#5b32ff);display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:900;color:#fff;box-shadow:0 0 18px rgba(124,92,255,.35)}
.brand h2{margin:0;font-size:18px;letter-spacing:.4px}
.brand p{margin:3px 0 0;font-size:9px;color:var(--muted);letter-spacing:.7px}
.roombox{padding:11px 14px 13px;border-top:1px solid rgba(34,42,58,.65)}
.roomline{display:flex;align-items:center;gap:7px;font-size:10px;color:var(--muted);margin-bottom:8px}
.dot{width:7px;height:7px;border-radius:50%;background:#25d6a4;box-shadow:0 0 8px rgba(37,214,164,.8)}
.room-url{height:31px;border:1px solid #2a3448;background:#090e17;border-radius:6px;display:flex;align-items:center;padding:0 9px;color:#aab6cb;font-family:var(--mono);font-size:10px;overflow:hidden;white-space:nowrap}
.room-actions{display:flex;gap:7px;margin-top:8px}
.btn{flex:1;height:30px;border-radius:6px;border:1px solid #2a3448;background:#151c29;color:#aab6cb;font-size:10px;display:flex;align-items:center;justify-content:center}
.btn.primary{background:linear-gradient(135deg,#8b5cff,#6b3dff);border-color:#8c66ff;color:#fff;font-weight:700}
.btn.primary.connected{background:rgba(37,214,164,.14);border-color:rgba(37,214,164,.38);color:#8fffe0;font-weight:600}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:0;padding:2px 8px}
.metric{display:flex;flex-direction:column;align-items:flex-start;justify-content:center;gap:3px;padding:10px 8px;border-bottom:1px solid rgba(34,42,58,.62);min-width:0}
.metric:nth-child(odd){border-right:1px solid rgba(34,42,58,.4)}
.metric:last-child{border-bottom:0}
.metric .k{font-size:10px;color:#8995aa;display:flex;align-items:center;gap:8px}
.metric .k i{display:block;width:13px;height:2px;border-radius:2px;background:var(--accent)}
.metric:nth-child(2) .k i{background:var(--cyan)} .metric:nth-child(3) .k i{background:var(--pink)} .metric:nth-child(4) .k i{background:var(--gold)}
.metric .v{font-family:var(--num);font-size:26px;font-weight:700;letter-spacing:.3px}
.metric .v small{font-family:var(--font);font-size:9px;font-weight:400;color:#78859b;margin-left:4px}
.metric .v.gold{color:var(--gold)}
.gift-panel{flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden}
.gift-head{display:flex;justify-content:space-between;align-items:center;padding:13px 15px 10px;border-bottom:1px solid rgba(34,42,58,.7)}
.gift-title{display:flex;align-items:baseline;gap:7px}
.gift-title strong{font-size:14px;color:#fff;letter-spacing:.2px}
.gift-title span{font-size:8px;letter-spacing:1.2px;color:var(--pink);font-weight:800}
.gift-count-badge{font-family:var(--mono);font-size:10px;color:#ff9cc5;background:rgba(255,45,149,.12);border:1px solid rgba(255,45,149,.28);border-radius:4px;padding:2px 7px}
.gift-list{flex:1;min-height:0;display:flex;flex-direction:column;padding:2px 11px 0;overflow:hidden}
.gift-row{flex:1;min-height:0;display:flex;align-items:center;padding:0 7px;border-bottom:1px solid rgba(34,42,58,.58);min-width:0}
.gift-oneline{display:flex;align-items:baseline;gap:4px;white-space:nowrap;min-width:0;width:100%}
.g-nick{font-size:18px;color:#f2f5ff;font-weight:800;max-width:92px;overflow:hidden;text-overflow:ellipsis;flex:none}
.g-send{font-size:13px;color:#9aa6ba;flex:none}
.g-gift{font-size:18px;color:#ff9cc5;font-weight:800;max-width:152px;overflow:hidden;text-overflow:ellipsis;flex:none}
.g-count{font-size:18px;color:#f2f5ff;font-weight:800;flex:none}
.g-value{font-size:12px;color:#9aa6ba;flex:none;margin-left:auto;white-space:nowrap}
.g-value b{font-size:18px;color:#ffe2a1;font-weight:800}
.gift-foot{flex:none;padding:9px 15px;color:#69758a;font-size:8px;border-top:1px solid rgba(34,42,58,.7);margin-top:0}
.side-foot{margin-top:auto;font-size:8px;color:#5f6c82;display:flex;justify-content:space-between;padding:0 3px}
"""


def feed_html() -> str:
    rows = []
    for kind, pay, fan, nick, content, cls in FEED:
        if kind == "entry":
            rows.append(
                '<div class="chat-row entry">'
                '<span class="lv">—</span>'
                f'<span class="lv">Lv.{fan}</span>'
                f'<span class="nick">{html.escape(nick)}</span>'
                f'<span class="content"><span class="entry-tag">入场</span>{html.escape(content)}</span>'
                "</div>"
            )
        else:
            c = f" {cls}" if cls else ""
            rows.append(
                f'<div class="chat-row{c}">'
                f'<span class="lv">Lv.{pay}</span>'
                f'<span class="lv">Lv.{fan}</span>'
                f'<span class="nick">{html.escape(nick)}</span>'
                f'<span class="content">{html.escape(content)}</span>'
                "</div>"
            )
    return "".join(rows)


def gifts_html() -> str:
    rows = []
    for pay, fan, nick, gift, count, value in GIFTS:
        rows.append(
            '<div class="gift-row"><div class="gift-oneline">'
            f'<b class="g-nick">{html.escape(nick)}</b>'
            '<span class="g-send">送出了</span>'
            f'<em class="g-gift">【{html.escape(gift)}】</em>'
            f'<span class="g-count">×{count}</span>'
            f'<span class="g-value">价值 <b>{value}</b>钻</span>'
            '</div></div>'
        )
    return "".join(rows)


def page() -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Aurora Dark · 80/20 侧栏布局</title>
<style>{CSS}</style>
</head>
<body>
<div class="app">
  <main class="panel chat-panel">
    <div class="chat-head">
      <div><div class="eyebrow">LIVE FEED</div><h1>弹幕<span>聊天 / 入场 / 礼物</span></h1></div>
      <div class="badge">{len(FEED)}</div>
    </div>
    <div class="legend">
      <b class="g">消费 35+ / 粉丝 15+</b>
      <b class="r">消费 40+ / 粉丝 18+</b>
      <b class="c">入场 15+</b>
      <b class="p">礼物 90+</b>
    </div>
    <div class="chat-table">
      <div class="chat-row chat-headrow"><span>消费等级</span><span>粉丝团</span><span>昵称</span><span>弹幕内容</span></div>
      {feed_html()}
    </div>
    <div class="chat-foot"><span>双击弹幕可复制完整内容</span><span>仅显示实时消息</span></div>
  </main>

  <aside class="sidebar">
    <div class="side-card brand">
      <div class="logo">弹</div>
      <div><h2>弹幕助手</h2><p>AURORA DARK · LIVE</p></div>
    </div>

    <div class="side-card roombox">
      <div class="roomline"><span class="dot"></span><span>已连接 · 在线 1,701 · 累计 2.6万</span></div>
      <div class="room-url">live.douyin.com/413532109481</div>
      <div class="room-actions"><div class="btn primary connected">已连接</div><div class="btn">断开</div><div class="btn">导出</div></div>
    </div>

    <div class="side-card metrics">
      <div class="metric"><div class="k"><i></i>当前在线</div><div class="v">1,701<small>人</small></div></div>
      <div class="metric"><div class="k"><i></i>本场涨粉</div><div class="v">+326<small>人</small></div></div>
      <div class="metric"><div class="k"><i></i>付费人数</div><div class="v">148<small>人</small></div></div>
      <div class="metric"><div class="k"><i></i>礼物总额</div><div class="v gold">286,420<small>钻</small></div></div>
    </div>

    <div class="side-card gift-panel">
      <div class="gift-head">
        <div class="gift-title"><span>GIFT FEED</span><strong>礼物 · 最近 5 条</strong></div>
        <span class="gift-count-badge">6</span>
      </div>
      <div class="gift-list">{gifts_html()}</div>
      <div class="gift-foot">仅显示整组累计 &gt;90 钻；总额/付费人数统计全部礼物</div>
    </div>

    <div class="side-foot"><span>CSV 已记录</span><span>21:42:18</span></div>
  </aside>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    path = OUT / "12-aurora-sidebar.html"
    path.write_text(page(), encoding="utf-8")
    print(path)
