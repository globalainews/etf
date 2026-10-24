# ETF 项目规范

## Agent 并发策略（硬性约束，必须遵守）
- 禁止并行启动多个 Task / sub-agent。所有子任务必须串行执行：完成一个再启动下一个。
- 不要同时发起多个 "Review" / "Explore" / "Investigate" 类的并行 agent。
- 如需分析多个文件，用单个 agent 顺序读取，不要为每个文件或每个目录开独立 agent。
- 并发 Task 数 = 1（强制串行）。如需并行，必须先向用户说明并征得同意。

## 代码 review 范围控制
- 一次 review 的文件数不超过 5 个。超过时分批进行，每批 ≤ 5 个文件。
- 不要把整个目录或整个代码库一次性读入上下文。如需了解全局，先用 LS 看目录结构，再针对具体文件 Read。
- review memory 文件时，只读最近 7 天的 topics.md 和 project_memory.md，不要读所有历史 session_memory_*.jsonl 文件。
- 避免对 node_modules / .git / dist / build / __pycache__ 等目录做任何读取操作。
- 单次任务上下文目标 < 30K token。如需更多信息，分批进行而非一次性塞入。

## Prompt 大小上限
- 任何 Task / sub-agent 的输入 prompt 不超过 50K token。超过就分批拆解任务。
- 发起请求前预估 prompt 大小；若单文件超过 10K 行，使用 offset/limit 分段读取而非整体读入。
- 向 FreeToken 服务发请求时，max_tokens 参数显式设为实际需要的值（建议 8192），不要使用 SDK 默认的 32K/64K。

## Conversation Compacting 限制
- 不要主动触发 conversation compacting。
- 上下文接近上限时，优先结束当前任务并总结输出，而不是触发 compact 后继续。
- 如必须 compact，确保 compact 期间不向 FreeToken 发起新请求，避免请求挂起。

## FreeToken 服务调用规范

- 优先保持 prompt 精简；串行发请求；流式响应必须及时消费，不要挂起。


## 数据准确性铁律

### 1. 所有数据必须从源核实
- 报告中的任何数字，必须在 `etf.db` 中有对应原始记录（`archive/` 下的 xlsx/csv 为迁移时点 2026-07-16 冻结的审计备份）
- 不得仅依赖中间脚本输出或缓存结果
- 每个数值须精确到至少2位小数（亿份），不得随意四舍五入

### 2. 变化量必须结合历史趋势判断
- **单点数据无意义**：某日某基金变化+100亿，必须查看其过去20-30个交易日的日均变化
- **区分"趋势延续"与"异常突变"**：
  - 如果日变化≈近期日均变化 → 这是趋势延续，不是新闻
  - 如果日变化远超近期日均变化（如>3倍标准差或>3倍均值） → 这才是异常事件
- **绝不将长期持续的变化归因为特定日期的事件**

### 3. 结论必须依据数据，不能预设叙事
- 先看数据全貌 → 再形成判断 → 最后写结论（不可颠倒）
- 报告中的每个"关键发现"必须有至少3个独立数据点支撑
- 对于精确整数变化（如恰好-5.00亿），必须查历史数据确认是否为规律性行为
- 使用"可能"、"或与……有关"等措辞表达不确定性，但不得用模糊语言掩盖数据不支持的观点

### 4. 交叉验证检查清单
生成任何分析报告前，对TOP5增减的每只基金执行以下检查：
- [ ] 该基金过去30个交易日的每日变化序列
- [ ] 目标日期的变化是否显著偏离其历史均值
- [ ] 同类基金（同主题/同市场）是否有一致性行为
- [ ] 精确整数变化是否为长期规律（暗示大机构的系统性申赎）

### 5. 报告修正义务
- 一旦发现报告中基于不完整数据得出错误结论，必须立即修正并说明原因
- 修正时保留原始错误作为对比，让读者了解修正了什么

---

## 数据源

**唯一数据源：`etf.db`（SQLite，项目根目录）**。2026-07-16 从 xlsx/csv 迁移而来，旧文件冻结于 `archive/`（含历史简称变更，仅作审计备份，不再更新）。

| 表/视图 | 内容 | 更新方式 |
|---------|------|----------|
| `fund_scale` | 每日每只ETF的份额（份），主键(date,code) | `python daily_update.py`（自动补齐缺失日期） |
| `fund_price` | 每日每只ETF的收盘价，主键(date,code) | `python tdx_price_update.py`（通达信，主路径） |
| `funds` | 基金维度：代码/简称/类型/市场/发行年份/行业主题 | 随份额更新自动刷新 |
| `v_total_scale` | 视图：每日总规模(亿份)/总金额(亿元)/基金数量 | 实时计算，无需重建 |

数据来源 API：
- 上交所 ETF 规模：`ak.fund_etf_scale_sse()`
- 深交所 ETF 规模：`ak.fund_scale_daily_szse()`
- ETF 价格：通达信本地 `.day` 文件（主）；`ak.fund_etf_hist_em()` 东方财富（备，daily_update.py 内）

日期格式 `'YYYY-MM-DD'` TEXT，基金代码 6位零填充 TEXT。查询示例：

```python
import pandas as pd
import db
conn = db.connect()
df = pd.read_sql_query(
    "SELECT * FROM fund_scale WHERE code='510300' ORDER BY date", conn)
```

## 常用操作

```bash
# 更新份额（自动检测并补齐所有缺失日期）
python daily_update.py --scale-only

# 更新指定日期
python daily_update.py --date 20260703

# 预览模式（不写入）
python daily_update.py --date 20260703 --dry-run

# 更新价格（通达信）
python tdx_price_update.py

# 查看数据库概况
python db.py

# 刷新行业主题（主题xlsx有更新时）
python db.py --import-theme 基金规模_含行业主题.xlsx

# 备份数据库
python -c "import sqlite3; sqlite3.connect('etf.db').execute(\"VACUUM INTO 'backups/etf_YYYYMMDD.db'\")"
```

注意：本机 PATH 里第一个 `python` 是损坏的 venv，运行脚本一律用 `F:\anaconda3\python`。
