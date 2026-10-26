# 「ETF龙虎榜」短视频素材接口规范 v1.0

## 1. 目标与边界

本规范用于外部数据程序向 Global 速览短视频程序投递「ETF龙虎榜」每日素材。外部程序负责：

- 计算当个交易日 ETF 份额增加 TOP5 和份额减少 TOP5；
- 生成四个竖屏 HTML 页面；
- 生成一份包含权威榜单数据及逐页口播的 JSON；
- 全部文件写完并校验后，最后写入就绪文件。

视频程序负责校验、页面渲染、TTS、字幕、转场、合成和输出，不再重新计算榜单或改写口播。

> 这里的榜单指标固定为“基金份额变化”，不是成交额、成交量、资金净流入或基金资产净值。若未来增加其他指标，必须升级 `schema_version` 或使用新的 `content_type`，不能复用本契约偷换含义。

## 2. 日期与目录

根目录固定为 `ETF`，子目录为实际交易日，使用 8 位数字 `YYYYMMDD`：

```text
ETF/
└── 20261023/
    ├── etf_longhubang.json
    ├── page_01_cover.html
    ├── page_02_increase.html
    ├── page_03_decrease.html
    ├── page_04_close.html
    ├── assets/                         # 可选，仅存放本包使用的本地素材
    │   ├── logo.png
    │   └── background.webp
    └── _READY.json                     # 必须最后写入
```

目录日期必须等于 JSON 的 `trade_date` 去掉连字符。周末、节假日或无有效数据时不创建普通日期包；如调度系统要求每日有结果，可只写 `_SKIP.json`，格式见第 10 节。

所有文件名使用小写 ASCII；禁止空格、中文文件名、绝对路径和 `..`。所有文本文件统一 UTF-8（无 BOM），换行推荐 LF。

## 3. 必需文件及固定含义

| 文件名 | 页面 ID | 内容 | 必需 |
|---|---|---|---|
| `etf_longhubang.json` | — | 权威榜单数据、逐页口播、渲染参数 | 是 |
| `page_01_cover.html` | `cover` | 日期、栏目名、当日钩子 | 是 |
| `page_02_increase.html` | `increase_top5` | 份额增加 TOP5 | 是 |
| `page_03_decrease.html` | `decrease_top5` | 份额减少 TOP5 | 是 |
| `page_04_close.html` | `close` | 当日摘要、口径和风险提示 | 是 |
| `_READY.json` | — | 完整包就绪信号及内容摘要 | 是，且最后写入 |

v1 不允许增加、删除或调换页面。这样页面、口播、字幕和成片时间线可以稳定按 `sequence=1..4` 对齐。

## 4. 权威 JSON 文件

文件名固定为 `etf_longhubang.json`。正式 JSON Schema 位于 [`docs/schemas/etf_longhubang.v1.schema.json`](schemas/etf_longhubang.v1.schema.json)，完整示例位于 [`docs/examples/etf_longhubang/etf_longhubang.json`](examples/etf_longhubang/etf_longhubang.json)。

### 4.1 顶层结构

```json
{
  "schema_version": "1.0",
  "content_type": "etf_longhubang",
  "package_id": "etf_longhubang_20261023_v1",
  "trade_date": "2026-10-23",
  "publish_date": "2026-10-24",
  "timezone": "Asia/Shanghai",
  "generated_at": "2026-10-24T06:05:00+08:00",
  "title": "ETF龙虎榜",
  "metric": { "name": "share_change", "unit": "share", "period": "1d" },
  "source": { "provider": "数据供应方", "dataset": "ETF日份额", "as_of": "2026-10-23T18:00:00+08:00" },
  "rankings": { "increase": [], "decrease": [] },
  "pages": [],
  "compliance": { "disclaimer": "数据仅供参考，不构成投资建议。" }
}
```

关键约束：

- `schema_version` 固定为字符串 `1.0`；消费者遇到未知版本必须拒绝，不能猜测兼容。
- `package_id` 在同一次投递中唯一；同日修订时末尾版本递增，如 `_v2`。
- `trade_date` 是榜单所属交易日；`publish_date` 是计划发布日，两者可不同。
- `timezone` v1 固定为 `Asia/Shanghai`。
- `metric.name` 固定为 `share_change`，`metric.unit` 固定为 `share`，表示单位“份”。
- `rankings.increase` 与 `rankings.decrease` 均必须恰好 5 条。
- `pages` 必须恰好 4 条，且顺序、页面 ID、HTML 文件名均固定。

### 4.2 榜单条目

```json
{
  "rank": 1,
  "fund_code": "510300.SH",
  "fund_name": "沪深300ETF",
  "exchange": "SSE",
  "share_change": 125000000,
  "shares_current": 8450000000,
  "shares_previous": 8325000000,
  "share_change_pct": 1.5015,
  "display_value": "+1.25亿份"
}
```

字段口径：

- `fund_code` 使用带市场后缀的唯一代码，如 `510300.SH`、`159915.SZ`。
- `exchange` 只允许 `SSE` 或 `SZSE`。
- `share_change = shares_current - shares_previous`，以“份”为单位。
- 增加榜 `share_change > 0`；减少榜 `share_change < 0`。
- `share_change_pct = share_change / shares_previous * 100`，单位为百分比数值；例如 `1.5` 表示 `1.5%`，不是 `0.015`。
- `display_value` 是页面显示字符串。增加榜必须以 `+` 开头，减少榜必须以 `-` 开头；数值应由 `share_change` 格式化得到。
- 每个榜单按 `abs(share_change)` 从大到小排序，`rank` 必须连续为 1—5。
- 同一 ETF 不得在同一榜单重复，也不得同时出现在增加榜与减少榜。
- `shares_current`、`shares_previous` 和 `share_change_pct` 可为空，但只要提供就必须与 `share_change` 一致。不得用 `0` 代替缺失值。

### 4.3 页面与口播

每个 `pages[]` 元素同时定义页面文件和对应口播：

```json
{
  "sequence": 2,
  "page_id": "increase_top5",
  "page_type": "ranking",
  "html_file": "page_02_increase.html",
  "headline": "份额增加 TOP5",
  "tts_text": "先看份额增加榜。第一名……",
  "subtitle_sentences": [
    "先看份额增加榜。",
    "第一名是沪深300ETF，增加1.25亿份。"
  ],
  "min_duration_sec": 6,
  "max_duration_sec": 22
}
```

约束如下：

- `tts_text` 是 TTS 的唯一输入，必须是可直接朗读的自然中文，不允许 Markdown、HTML、换行控制指令或读音批注。
- TTS 文本中的数字应写成自然中文读法，例如“一点二五亿份”；代码可写为“510300”，但建议口播不读代码。
- `subtitle_sentences` 是人工确认过的字幕分句；顺序拼接并去除标点后，应与 `tts_text` 语义一致。
- 单条字幕建议不超过 32 个汉字，画面最多两行；不要把 ETF 名称拆到两条字幕中。
- `tts_text` 建议：封面 20—45 字，两个榜单页各 70—130 字，收尾 25—60 字；全片建议 45—70 秒。
- `min_duration_sec`、`max_duration_sec` 是页面时长边界。实际时长由 TTS 音频时长加尾部保护时间决定，并夹在这两个值之间；音频超过上限时整包质检失败，不做截断。
- 口播中的排名、名称、增减方向和份额值必须与 `rankings` 一致。允许为了节奏统一换算成“万份/亿份”，不允许改变有效数值。
- 收尾页必须口播风险提示或明确提示“份额变化不等于资金净流入”。

## 5. HTML 页面契约

四个 HTML 都必须满足：

- 设计画布为 `1080 × 1920`，竖屏 9:16；浏览器渲染视口也是 `1080 × 1920`。
- 页面必须自包含；CSS/JS 可内联，本地图片仅能通过相对路径 `assets/...` 引用。
- 禁止任何外网请求，包括 CDN 字体、远程图片、分析脚本和接口请求。
- 禁止自动播放音频或视频；禁止依赖鼠标、键盘、滚动、WebGL 和实时系统时间。
- 主要文字安全区：左右各至少 72 px，顶部至少 140 px，底部至少 220 px，避免被平台标题和控件遮挡。
- 正文最小字号建议 38 px，榜单 ETF 名称建议不小于 42 px，关键份额建议不小于 52 px。
- 页面显示的日期、ETF 名称、代码、排名和数值必须与 JSON 一致。
- 页面加载和字体完成后设置渲染就绪标记：

```html
<script>
Promise.all([document.fonts.ready]).then(() => {
  document.documentElement.dataset.renderReady = "true";
});
</script>
```

视频程序只在 `html[data-render-ready="true"]` 出现后截图/录制；10 秒仍未就绪则失败。

页面可以有 CSS 入场动画，但必须满足：

- 首帧不能全黑或全透明；
- 动画总长不超过 1.2 秒；
- 支持 `prefers-reduced-motion: reduce`，静态渲染时仍完整展示全部信息；
- 不得用动画改变榜单排序或最终数值。

## 6. `_READY.json` 就绪协议

生产者必须先写临时目录，例如 `ETF/.20261023.tmp/`；全部文件生成并自检完成后，将目录原子重命名为 `ETF/20261023/`，最后写 `_READY.json`。消费者只处理存在且合法的 `_READY.json`，并忽略 `.tmp` 目录。

```json
{
  "schema_version": "1.0",
  "content_type": "etf_longhubang",
  "package_id": "etf_longhubang_20261023_v1",
  "trade_date": "2026-10-23",
  "created_at": "2026-10-24T06:05:10+08:00",
  "main_file": "etf_longhubang.json",
  "main_file_sha256": "64位小写十六进制SHA-256",
  "file_count": 5
}
```

`file_count` 统计 `_READY.json` 之外的必需文件，即主 JSON 加四个 HTML，固定为 5；`assets` 下文件不计入。消费者必须校验主文件 SHA-256、`package_id` 和 `trade_date` 三者一致。

同日修订时不要覆盖消费者可能正在读取的目录。推荐先生成完整新包，再将旧目录改名归档，最后原子切换；`package_id` 版本必须递增。

## 7. 数据、口播、页面一致性检查

生产端在写 `_READY.json` 前，消费端在开始制片前，都必须执行以下校验：

1. 目录名、`trade_date`、`_READY.trade_date` 一致。
2. 主 JSON 通过 v1 Schema。
3. 增加榜和减少榜各 5 条，排名连续、符号正确、排序正确、代码不重复。
4. 若提供前后份额，验证 `current - previous = change`；允许的浮点误差不超过 0.5 份。
5. 四个 HTML 都存在，文件名和 `pages[].html_file` 完全一致。
6. HTML 不包含 `http://`、`https://`、协议相对 URL 或越界相对路径。
7. 页面正文能找到对应 ETF 名称和显示值；口播能找到对应 ETF 名称，并且方向和数值语义一致。
8. 每页在 1080×1920 下无横向滚动、文字溢出或底部遮挡。
9. `main_file_sha256` 匹配，且 `_READY.json` 是最后生成的文件。

任一必需检查失败时整包拒绝，不允许只做“增加榜”或自动少播一只 ETF。

## 8. 推荐成片顺序

| 时间线 | 页面 | 推荐内容 |
|---|---|---|
| 1 | 封面 | 栏目名、交易日、当日最大变化钩子 |
| 2 | 增加榜 | 从第 5 名到第 1 名或从第 1 名到第 5 名均可，但页面与口播顺序必须一致；推荐 5→1 增强悬念 |
| 3 | 减少榜 | 与增加榜使用相同播报顺序 |
| 4 | 收尾 | 最大增减摘要、数据口径、风险提示、关注引导 |

JSON 中的 `rank` 始终是榜单名次 1→5；如果口播选择 5→1，只改变 `tts_text` 和页面视觉呈现顺序，不改变数组排序和 `rank`。

## 9. 输出命名建议

视频程序的产物不属于外部投递包，建议写到独立输出目录：

```text
output/ETF/20261023/
├── ETF龙虎榜_20261023.mp4
├── ETF龙虎榜_20261023_cover.jpg
├── subtitles.srt
├── build_manifest.json
└── qa_report.json
```

这样不会把输入素材和派生产物混在一起，也便于同日重建和追溯。

## 10. 非交易日/无数据协议

非交易日不生成普通素材包。若调度器要求明确回执，可在对应日目录只写 `_SKIP.json`，且不能同时存在 `_READY.json`：

```json
{
  "schema_version": "1.0",
  "content_type": "etf_longhubang",
  "calendar_date": "2026-10-25",
  "status": "skipped",
  "reason_code": "non_trading_day",
  "reason": "非交易日"
}
```

`reason_code` v1 允许：`non_trading_day`、`source_unavailable`、`insufficient_data`。后两种应触发运营告警，不能被当作正常成功。

## 11. v1 验收标准

一份素材包只有同时满足以下条件才算可制片：

- `_READY.json` 存在且哈希正确；
- JSON Schema 校验通过；
- 增加/减少榜均为可信 TOP5；
- 四页 HTML 在 1080×1920 下成功渲染；
- 榜单数据、页面文字、口播与字幕四者一致；
- 口播总时长在 45—70 秒建议区间内；
- 风险提示存在，且未把份额变化表述为确定的资金流入或流出。

