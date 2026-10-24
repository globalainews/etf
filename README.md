# ETF 基金规模趋势分析与量化轮动策略

A quantitative ETF analysis toolkit built on SQLite — from daily data updates to factor-based strategy backtesting.

## Features

- **Daily Data Pipeline** — Fetches ETF share/scale data from SSE & SZSE via AKShare, and price data from TongDaXin local `.day` files (with EastMoney as fallback).
- **SQLite Database** — Single source of truth (`etf.db`) with normalized schema: fund metadata, daily shares, daily prices, and a total-scale view.
- **Web Dashboard** — Flask-based web app for interactive exploration of ETF scale trends, cross-theme comparison, and anomaly detection.
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
│   └── strategy/
│       ├── multi_factor_builder.py   # 4-dim factor computation (momentum, volume, flow, vol)
│       ├── signal_generator.py       # Signal generation from factor scores
│       └── backtest_engine.py        # T+1 backtest simulator with attribution
├── archive/                 # Historical CSV/Excel backups (frozen, audit-only)
├── data/                    # Strategy output data (events, candidates, universe)
├── docs/                    # Strategy documentation (Step 1–4)
├── templates/               # Web app HTML templates
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
# → http://127.0.0.1:5000
```

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
