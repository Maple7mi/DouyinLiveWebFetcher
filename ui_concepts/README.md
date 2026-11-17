# 弹幕助手 · 10 版 UI 方向

同一套功能、同一组模拟数据，只换视觉语言。每张预览都是 **1600×1000**，可直接放大对比。

## 怎么选

| # | 名称 | 风格 | 最适合 |
|---|---|---|---|
| 01 | Aurora Dark | 深色、低饱和蓝紫、细边框 | 长时间盯盘、夜间使用 |
| 02 | Warm Paper | 暖白纸面、衬线标题、极轻阴影 | 阅读、复盘、安静氛围 |
| 03 | Primer Grid | GitHub Primer、深色顶栏、紧凑表格 | 查 ID、查数据、工程感 |
| 04 | Discord Night | 深灰分层、左侧栏、聊天优先 | 习惯 Discord 的人 |
| 05 | Stream Deck | 黑紫、LIVE 标签、右侧大聊天区 | 直播控制台、投屏 |
| 06 | Frosted Glass | 半透明玻璃、大圆角、柔和渐变 | 平板、轻量、女性主播 |
| 07 | Neon Grid | 黑底青霓虹、洋红高亮、切角卡片 | 游戏 / 电竞直播间 |
| 08 | Cream Bloom | 奶油白、豆沙粉、圆润卡片 | 陪伴型、生活方式直播 |
| 09 | Material Bloom | Material You、淡紫 tonal 卡片 | 移动端习惯、干净明亮 |
| 10 | Terminal Pro | 等宽字体、磷光绿、数据终端 | 逐条复盘、数据党 |

## 我的建议

- **最稳妥**：06 Frosted Glass 或 09 Material Bloom。白底、字大、分区清楚，平板看最舒服，也比现在的版本更有设计感。
- **如果她喜欢深色**：01 Aurora Dark。比现在的深色版更克制，不会像游戏面板。
- **如果要直播氛围**：05 Stream Deck 或 07 Neon Grid，但建议只作为「主题」，默认仍用浅色。
- **如果要长期记录/查数据**：03 Primer Grid 或 10 Terminal Pro。

最终落地时建议做 **明暗双主题**：浅色默认（06 或 09），深色可选（01），共用同一套布局，避免维护两套界面。

## GitHub 参考

- shadcn-admin：https://github.com/satnaing/shadcn-admin
- shadcn/ui：https://github.com/shadcn-ui/ui
- GitHub Primer：https://github.com/primer/design
- AFFiNE（纸感文档）：https://github.com/toeverything/AFFiNE
- Fluent UI：https://github.com/microsoft/fluentui
- Material Web：https://github.com/material-components/material-web
- Grafana：https://github.com/grafana/grafana
- Tabler：https://github.com/tabler/tabler

Discord、Twitch 是产品风格参考，不是开源仓库。

## 说明

- 数据为模拟：在线 12,846、涨粉 +326、付费 148、礼物总额 286,420 钻。
- 保留了真实功能：消费等级/粉丝团等级/昵称/弹幕、>15 级入场、>90 钻礼物、按 ID 汇总、涨粉/付费/在线统计。
- 预览中的高亮行是刻意放的样例：浅金 = 消费>35 或粉丝团≥15；浅红 = 消费>40 或粉丝团≥18。
- 选定编号后，我再把对应风格落到 Tkinter 真实界面，并重新打包便携版。
