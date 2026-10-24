# Database Audit Report
**Generated**: 2026-08-28 12:16:34  
**Backup**: [etf.db](./etf.db)  
---

## Schema
  - `fund_price.date`: TEXT PK NOT NULL
  - `fund_price.code`: TEXT PK NOT NULL
  - `fund_price.close`: REAL  NOT NULL
  - `fund_price.volume`: REAL  

### `funds`
  - `code`: TEXT PK 
  - `name`: TEXT  NOT NULL
  - `type`: TEXT  NOT NULL
  - `market`: TEXT  NOT NULL
  - `issue_year`: INTEGER  
  - `theme`: TEXT  NOT NULL

### `fund_scale`
  - `date`: TEXT PK NOT NULL
  - `code`: TEXT PK NOT NULL
  - `shares`: REAL  NOT NULL

### `signal_events`
  - `date`: TEXT PK NOT NULL
  - `code`: TEXT PK NOT NULL
  - `event`: TEXT  NOT NULL
  - `close`: REAL  
  - `share_z_20`: REAL  
  - `price_dev_20`: REAL  

### `backtest_trades`
  - `code`: TEXT PK NOT NULL
  - `name`: TEXT  NOT NULL
  - `scheme`: TEXT PK NOT NULL
  - `entry_date`: TEXT PK NOT NULL
  - `exit_date`: TEXT  NOT NULL
  - `entry_price`: REAL  
  - `exit_price`: REAL  
  - `hold_days`: INTEGER  
  - `exit_reason`: TEXT  NOT NULL
  - `return_pct`: REAL  
  - `signal_z`: REAL  
  - `signal_dev`: REAL  
## Table Statistics
### `funds`
- Rows: 1,731
### `fund_scale`
- Rows: 1,342,840
- Date range: 2019-01-02 ~ 2026-08-26
### `fund_price`
- Rows: 1,459,379
- Date range: 1991-04-03 ~ 2026-08-27
- Volume: , 1,432,256 (98.1%) with volume
### `signal_events`
- Rows: 39
- Date range: 2025-04-07 ~ 2026-06-30
### `backtest_trades`
- Rows: 42

## fund_price Coverage by Code Type
### 大盘指数
- 000300: [2005-01-04,2026-08-27] n=5,259 vol%=100.0%
- 000510: [2004-12-31,2026-08-27] n=5,260 vol%=100.0%
- 000016: [2004-01-02,2026-08-27] n=5,502 vol%=100.0%
- 000852: [2005-01-04,2026-08-27] n=5,171 vol%=100.0%
- 000001: [1995-06-01,2026-08-27] n=7,584 vol%=100.0%
- 399001: [1991-04-03,2025-04-30] n=5,571 vol%=100.0%

### ETF (按前缀分组)
- 51xxxx (上交所): 422 只, [2005-02-23,2026-08-27] 共 630,649 行, 630,649 行有成交量 (100.0%)
- 56xxxx: 257 只, [2021-06-28,2026-08-27] 共 141,741 行, 141,741 行有成交量 (100.0%)
- 58xxxx: 141 只, [2020-11-16,2026-08-27] 共 63,333 行, 51,969 行有成交量 (82.1%)
- 588xxx: 84 只, [2020-11-16,2026-08-27] 共 51,969 行, 51,969 行有成交量 (100.0%)
- 15xxxx (深交所): 727 只, [2006-04-24,2026-08-27] 共 573,550 行, 573,550 行有成交量 (100.0%)

## 目标 7 只 ETF (2019+)
| code | 日期范围 | 价格行数 | 有成交量 | 成交量% |
|------|----------|----------|----------|--------|
| 512890 | [2019-01-18,2026-08-27] | 1,845 | 1,845 | 100.0% |
| 512000 | [2019-01-02,2026-08-27] | 1,857 | 1,857 | 100.0% |
| 512880 | [2019-01-02,2026-08-27] | 1,857 | 1,857 | 100.0% |
| 513180 | [2021-05-25,2026-08-27] | 1,278 | 1,278 | 100.0% |
| 513130 | [2021-06-01,2026-08-27] | 1,273 | 1,273 | 100.0% |
| 515120 | [2021-01-04,2026-08-27] | 1,370 | 1,370 | 100.0% |
| 159740 | [2021-05-27,2026-08-27] | 1,276 | 1,276 | 100.0% |

## fund_scale 最新一期
- 日期: 2026-08-26
- 基金数: 1,621
- 总份额: 31718.65 亿份
- 总金额: 44928.61 亿元


-> database_audit.md written
