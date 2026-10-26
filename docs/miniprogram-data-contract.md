# ETF份额龙虎榜数据契约

版本：`1.0`；核对日期：2026-10-26。

## 本地字段映射

| SQLite | 云字段 | 类型/单位 | 说明 |
|---|---|---|---|
| `funds.code` | `code` | 6位字符串 | 保留前导0 |
| `funds.name` | `name` | 字符串 | 不生成拼音或简称 |
| `funds.market` | `market` | 字符串 | 上海/深圳 |
| `funds.type` | `fund_type` | 字符串 | 原始ETF类型 |
| `funds.theme` | `sector` | 可空字符串 | 当前唯一分类来源；空值不推断 |
| `fund_scale.date` | `date/statistic_date` | `YYYY-MM-DD` | 数据源统计日期，不使用同步日或采集时钟替代 |
| `fund_scale.shares` | `shares` | 份 | 展示时除以 `1e8` 为亿份 |
| `fund_price.close` | `close` | CNY | 当前上游口径为前复权收盘价 |
| `fund_price.volume` | 暂不发布 | - | 首版查询不需要 |

`fund_scale.date` 是当前数据库能够提供的实际统计日期。同步器从不把运行日期、文件修改时间或采集时间写成行情日期。现有上游没有独立的公告披露时间字段，也没有跨境ETF滞后天数和公司行为调整表，因此发布状态明确标记 `disclosure_date_verification=SOURCE_DATE_ONLY`、`special_event_status=NO_RELIABLE_SOURCE`。这两项未补齐前，不宣称已独立核验披露时点，也不擅自调整或插值。

## 统一计算口径

- `delta_shares = 当日份额 - 前一有效统计日份额`。
- `change_rate = delta_shares / 基期份额`；基期缺失、非有限值或 `<=0` 时返回 `null` 和状态码。
- `price_change_rate` 使用同一 `statistic_date/baseline_date`。
- 连续计数逐个统计日向前检查，0变化和任一缺失都会中断。
- 异动倍数使用当日绝对变化率除以前20个有效统计日绝对变化率中位数，历史窗口不含当日；有效历史少于20或中位数为0时不计算。
- 排名使用未舍入数值；数值并列时显示并列名次，再按代码升序稳定排列。
- 板块指标为板块内可计算ETF变化率中位数；不会把不同ETF份额相加解释成资金流。
- 所有页面统一提示：`份额变化不代表未来涨跌，不等同资金净流入。`

## 批次与修订

派生结果以 `release_id` 整批保存；`publish_status/current` 是唯一当前指针。历史记录是按 `code + date + release_seq` 保存的不可变修订：详情查询只读取 `release_seq <= current.release_seq`，同日期取最高修订序号。失败批次的修订在下一成功批次会重新生成更高序号并覆盖查询结果。

首次同步默认最近90个可用统计日。日常同步只上传最新日；历史更正使用 `--correction-from YYYY-MM-DD` 明确边界，并重算最新连续、异动、板块和榜单快照。

