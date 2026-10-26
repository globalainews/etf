# ETF 基金规模趋势分析与量化轮动策略

A quantitative ETF analysis toolkit built on SQLite — from daily data updates to factor-based strategy backtesting.

## Features

- **Daily Data Pipeline** — Fetches ETF share/scale data from SSE & SZSE via AKShare, and price data from TongDaXin local `.day` files (with EastMoney as fallback).
- **SQLite Database** — Single source of truth (`etf.db`) with normalized schema: fund metadata, daily shares, daily prices, and a total-scale view.
- **Web Dashboard** — Flask-based web app for interactive exploration of ETF scale trends, cross-theme comparison, anomaly detection, and the read-only ETF share-ranking workspace.
- **Multi-Factor Strategy** — 4-dimensional factor library: momentum, volume impulse, smart money flow (share-price divergence), and volatility.
- **Backtest Engine** — T+1 simulation with parameter optimization (Optuna), performance attribution, and risk analysis.

## Project Structure

```
├── daily_update.py          # Daily ETF scale/price update script
├── tdx_price_update.py      # TongDaXin .day file parser (price data)
├── db.py                    # SQLite schema & database management
├── app.py                   # Flask web dashboard
├── backtest_optimize.py     # Standalone dual-factor backtest + Optuna optimization
├── src/
│   ├── etf_rankings/        # Shared day/week ranking service (web + video prototype)
│   └── strategy/
│       ├── multi_factor_builder.py   # 4-dim factor computation (momentum, volume, flow, vol)
│       ├── signal_generator.py       # Signal generation from factor scores
│       └── backtest_engine.py        # T+1 backtest simulator with attribution
├── archive/                 # Historical CSV/Excel backups (frozen, audit-only)
├── data/                    # Strategy output data (events, candidates, universe)
├── docs/                    # Strategy documentation (Step 1–4)
├── templates/               # Web app HTML templates
├── static/                  # Isolated ranking workspace CSS/JavaScript
├── tests/                   # Validation tests
├── etf.db                   # [not in git] Primary SQLite database
└── reports/                 # [not in git] Generated PDF/Excel reports
```

## Database

`etf.db` is the sole data source, migrated on 2026-07-16 from xlsx/csv files (archived in `archive/`).

| Table | Description |
|-------|-------------|
| `funds` | Fund metadata: code, name, type, market, issue_year, theme |
| `fund_scale` | Daily shares per ETF (date, code, shares) |
| `fund_price` | Daily closing price per ETF (date, code, close, volume) |
| `v_total_scale` | View: daily total scale (shares & amount), fund count |

**Query example** (pandas):

```python
import pandas as pd, db

conn = db.connect()
df = pd.read_sql_query(
    "SELECT * FROM fund_scale WHERE code='510300' ORDER BY date", conn)
```

## Quick Start

### Prerequisites

- Python 3.10+
- Conda or pip

### Installation

```bash
pip install akshare pandas flask optuna matplotlib
```

### Update Data

```bash
# Auto-detect and fill all missing dates (default)
python daily_update.py

# Update only the scale data (skip price)
python daily_update.py --scale-only

# Update a specific date
python daily_update.py --date 20260629

# Preview mode (no writes)
python daily_update.py --date 20260629 --dry-run

# Update prices from TongDaXin
python tdx_price_update.py
```

### Web Dashboard

```bash
python app.py
# → http://127.0.0.1:5010
```

### ETF份额龙虎榜（一期）

在页面顶部切换到“ETF份额龙虎榜”，可查看单日和5日周榜。该区域通过
`GET /api/rankings`（或 `GET /api/rankings?date=YYYY-MM-DD`）从 `etf.db`
建立只读连接；数据库更新后会自动按文件时间和大小失效缓存。

- 日榜以相邻有效交易日的份额变化乘当日收盘价计算“收盘参考市值”。这不是资金净流入。
- 周榜用6个完整份额观测点计算5个日变化；连续增加榜必须5日均为正。
- 缺收盘价的ETF仍计入增减统计，但不参加参考市值排序；主题/行业榜一期未启用。

### Backtest

```bash
python backtest_optimize.py
```

## Data Sources

| Data | Source | Method |
|------|--------|--------|
| ETF scale (shares) | SSE & SZSE | `ak.fund_etf_scale_sse()` / `ak.fund_scale_daily_szse()` |
| ETF price | TongDaXin `.day` files (primary), EastMoney (fallback) | `tdx_price_update.py` / `daily_update.py` |

## Quant Strategy (Step 1–4)

A complete multi-factor ETF rotation strategy, documented in `docs/`:

1. **Step 1** — Factor library: momentum, volume impulse, smart money flow, volatility
2. **Step 2** — Signal generator: factor scoring, cross-sectional ranking, signal generation
3. **Step 3** — Backtest engine: T+1 simulation, performance attribution, risk analysis
4. **Step 4** — Parameter optimization via Optuna

```python
from src.strategy.multi_factor_builder import compute_all_factors
from src.strategy.signal_generator import generate_signals
from src.strategy.backtest_engine import run_backtest

factors = compute_all_factors(codes, start_date='2019-01-01')
signals = generate_signals(factors)
run_backtest(start_date='2019-04-01')
```

## License

MIT

## ETF份额龙虎榜：Web＋微信小程序

仓库现包含10页原生微信小程序、统一JavaScript云函数和只读SQLite同步器：

```
miniprogram/              # 原生WXML/WXSS/JavaScript，demo/real硬隔离
cloudfunctions/etf-api/  # 排行、搜索、详情、自选和权益查询
sync/etf_sync/           # SQLite物化、校验、CloudBase同步和视频素材
cloud-database/          # 索引和客户端数据库安全规则
docs/miniprogram-*.md    # 数据契约、容量、部署与Windows运行说明
```

首次建立本地派生结果（排名、连续、异动、板块）：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --full --dry-run
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --full
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --verify
```

启动 Web 后访问 `http://127.0.0.1:5010/ranking-app`：

```powershell
F:\anaconda3\python.exe -X utf8 app.py
```

日常数据更新后先发布本地增量，再同步云数据库：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --incremental
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.cli --dry-run
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.cli
```

Windows日常操作也可直接双击项目根目录的批处理文件：

1. `计算龙虎榜派生表.bat`：在份额和价格更新完成后，计算新增交易日并校验本地发布批次；不会写入CloudBase。
2. `同步龙虎榜到云数据库.bat`：先校验本地批次和执行云端dry-run，确认输入 `Y` 后才同步并切换云端发布指针。

第二个脚本从当前系统环境变量读取 `CLOUDBASE_ENV_ID` 与 `CLOUDBASE_API_KEY`；若未设置，则读取仓库外的 `%USERPROFILE%\.etf-cloudbase.env`。该文件每行一个 `KEY=VALUE`，不得提交到仓库。

导出每日视频素材数据和7个HTML场景：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.video_cli --render data
# 需要先启动 Web；截图输出为1080×1920 PNG
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.video_cli --render stills
```

本地验收：

```powershell
F:\anaconda3\python.exe -X utf8 -m pytest tests\test_miniprogram_compute.py tests\test_miniprogram_sync.py tests\test_local_materialization.py tests\test_mini_web_api.py -q --basetemp .codex_stage\pytest-mini
npm test --prefix cloudfunctions\etf-api
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.cli --dry-run --initial
```

默认 [miniprogram/config/env.js](miniprogram/config/env.js) 为 `demo`，无云凭据即可导入微信开发者工具。本地物化、Web、视频和云同步命令见 [本地Web与视频运行说明](docs/local-ranking-web.md)；小程序部署见 [Windows运行、部署与验收](docs/miniprogram-runbook.md)。
