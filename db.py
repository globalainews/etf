# -*- coding: utf-8 -*-
"""ETF SQLite 数据库共享模块

etf.db 是项目唯一数据源（2026-07-16 起，替代 基金规模.xlsx / etf_prices.csv /
etf_total_scale.csv，旧文件冻结于 archive/ 作审计备份）。

表结构：
  funds        基金维度表（每只基金一行，name/type/market 为最新值）
  fund_scale   份额事实表，主键 (date, code)
  fund_price   收盘价事实表，主键 (date, code)
  v_total_scale 视图：每日总规模(亿份)/总金额(亿元)/基金数量/有价基金数

用法（命令行）：
  python db.py                          # 打印数据库概况
  python db.py --import-theme <xlsx>    # 从含行业主题的xlsx刷新 funds.theme
"""

import os
import sqlite3

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, 'etf.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS funds (
    code       TEXT PRIMARY KEY,
    name       TEXT NOT NULL DEFAULT '',
    type       TEXT NOT NULL DEFAULT '',
    market     TEXT NOT NULL DEFAULT '',
    issue_year INTEGER,
    theme      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS fund_scale (
    date   TEXT NOT NULL,
    code   TEXT NOT NULL,
    shares REAL NOT NULL,
    PRIMARY KEY (date, code)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS fund_price (
    date  TEXT NOT NULL,
    code  TEXT NOT NULL,
    close REAL NOT NULL,
    volume REAL,
    PRIMARY KEY (date, code)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_scale_code_date ON fund_scale(code, date);
CREATE INDEX IF NOT EXISTS idx_price_code_date ON fund_price(code, date);

-- 策略标记数据（第 8 步研究产出，2026-08 from CSV，只读展示用途）
CREATE TABLE IF NOT EXISTS signal_events (
    date          TEXT NOT NULL,
    code          TEXT NOT NULL,
    event         TEXT NOT NULL DEFAULT '',
    close         REAL,
    share_z_20    REAL,
    price_dev_20  REAL,
    PRIMARY KEY (date, code)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS backtest_trades (
    code        TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    scheme      TEXT NOT NULL,
    entry_date  TEXT NOT NULL,
    exit_date   TEXT NOT NULL,
    entry_price REAL,
    exit_price  REAL,
    hold_days   INTEGER,
    exit_reason TEXT NOT NULL DEFAULT '',
    return_pct  REAL,
    signal_z    REAL,
    signal_dev  REAL,
    PRIMARY KEY (code, entry_date, scheme)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_marker_signal_code_date ON signal_events(code, date);
CREATE INDEX IF NOT EXISTS idx_trade_code_scheme ON backtest_trades(code, scheme);

CREATE VIEW IF NOT EXISTS v_total_scale AS
SELECT d.date,
       d.total_shares_yi,
       a.total_amount_yi,
       d.fund_count,
       a.priced_count
FROM (
    SELECT date,
           ROUND(SUM(shares) / 1e8, 2) AS total_shares_yi,
           COUNT(*) AS fund_count
    FROM fund_scale
    WHERE date >= '2025-01-01'
    GROUP BY date
) d
LEFT JOIN (
    SELECT s.date,
           ROUND(SUM(s.shares * p.close) / 1e8, 2) AS total_amount_yi,
           COUNT(*) AS priced_count
    FROM fund_scale s
    JOIN fund_price p ON p.date = s.date AND p.code = s.code
    GROUP BY s.date
) a ON a.date = d.date
ORDER BY d.date;
"""


def connect(db_path=DB_PATH):
    """打开数据库连接（自动建表）"""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    ensure_schema(conn)
    return conn


def ensure_schema(conn):
    conn.executescript(SCHEMA)
    conn.commit()
    # 升级：已有库可能没有 volume 列
    try:
        conn.execute("ALTER TABLE fund_price ADD COLUMN volume REAL")
        conn.commit()
    except Exception:
        pass  # 已存在则忽略


def norm_date(v):
    """日期统一为 'YYYY-MM-DD'（接受 YYYYMMDD / YYYY-MM-DD / Timestamp）"""
    import pandas as pd
    return pd.to_datetime(v).strftime('%Y-%m-%d')


# ===================================================================
#  读取助手
# ===================================================================

def scale_dates(conn):
    """份额表中所有日期（升序，'YYYY-MM-DD'）"""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM fund_scale ORDER BY date")]


def max_price_date(conn):
    """价格表最大日期，空表返回 None"""
    return conn.execute("SELECT MAX(date) FROM fund_price").fetchone()[0]


def price_max_date_by_code(conn):
    """每只基金的最新价格日期 {code: 'YYYY-MM-DD'}"""
    return dict(conn.execute(
        "SELECT code, MAX(date) FROM fund_price GROUP BY code"))


def fund_codes(conn):
    """全部基金代码（升序）"""
    return [r[0] for r in conn.execute("SELECT code FROM funds ORDER BY code")]


# ===================================================================
#  写入助手（executemany + 单事务）
# ===================================================================

def _col_or_empty(df, col):
    if col in df.columns:
        return df[col].fillna('').astype(str)
    return [''] * len(df)


def upsert_funds(conn, day_df, default_year):
    """插入/更新基金维度表

    新基金：issue_year = default_year（首次出现日期的年份）。
    已有基金：更新 name/type/market 为本批数据的值，保留 issue_year/theme。
    注意：回填历史日期时会用旧简称覆盖，下次更新最新日期时会自动纠正（改名极少）。
    调用方需自行 commit（通常在 with conn: 事务内调用）。
    """
    codes = day_df['基金代码'].astype(str).str.zfill(6)
    rows = list(zip(
        codes,
        _col_or_empty(day_df, '基金简称'),
        _col_or_empty(day_df, '类型'),
        _col_or_empty(day_df, '市场'),
        [default_year] * len(day_df),
    ))
    conn.executemany("""
        INSERT INTO funds(code, name, type, market, issue_year)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            name   = excluded.name,
            type   = excluded.type,
            market = excluded.market
    """, rows)
    return len(rows)


def replace_scale_for_date(conn, date_str, day_df):
    """单事务替换某一日的全部份额数据，并同步基金维度表

    day_df 列: 基金代码/基金简称/类型/基金规模(份)/市场（日期列可有可无）
    返回写入行数。
    """
    d = norm_date(date_str)
    codes = day_df['基金代码'].astype(str).str.zfill(6)
    shares = day_df['基金规模(份)'].astype(float)
    rows = list(zip([d] * len(day_df), codes, shares))
    with conn:
        conn.execute("DELETE FROM fund_scale WHERE date = ?", (d,))
        conn.executemany(
            "INSERT INTO fund_scale(date, code, shares) VALUES (?, ?, ?)", rows)
        upsert_funds(conn, day_df, default_year=int(d[:4]))
    return len(rows)


def upsert_prices(conn, price_df):
    """按 (date, code) 覆盖写入价格数据

    price_df 列: 基金代码/日期/收盘价/[成交量]
    返回写入行数。
    """
    import pandas as pd
    codes = price_df['基金代码'].astype(str).str.zfill(6)
    dates = pd.to_datetime(price_df['日期']).dt.strftime('%Y-%m-%d')
    closes = price_df['收盘价'].astype(float)

    if '成交量' in price_df.columns:
        vols = _num_col(price_df, '成交量')
        rows = list(zip(dates, codes, closes, vols))
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO fund_price(date, code, close, volume) VALUES (?, ?, ?, ?)",
                rows)
    else:
        rows = list(zip(dates, codes, closes, [None] * len(dates)))
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO fund_price(date, code, close, volume) VALUES (?, ?, ?, ?)",
                rows)
    return len(rows)


def _num_col(df, col):
    """列存在则转数值，缺列则为全 NaN（长度与 df 一致）"""
    import pandas as pd
    if col in df.columns:
        return pd.to_numeric(df[col], errors='coerce')
    return pd.Series([float('nan')] * len(df))


def upsert_signal_events(conn, sig_df):
    """覆盖写入 E2 信号事件表（signal_events）

    sig_df 列: code/date/event/close/share_z_20/price_dev_20
    返回写入行数。按 (date, code) 覆盖。
    """
    rows = list(zip(
        sig_df['date'].astype(str),
        sig_df['code'].astype(str).str.zfill(6),
        sig_df['event'].astype(str),
        _num_col(sig_df, 'close'),
        _num_col(sig_df, 'share_z_20'),
        _num_col(sig_df, 'price_dev_20'),
    ))
    with conn:
        conn.executemany("""
            INSERT INTO signal_events(date, code, event, close, share_z_20, price_dev_20)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, code) DO UPDATE SET
                event=excluded.event, close=excluded.close,
                share_z_20=excluded.share_z_20, price_dev_20=excluded.price_dev_20
        """, rows)
    return len(rows)


def upsert_backtest_trades(conn, trade_df):
    """覆盖写入回测交易表（backtest_trades）

    trade_df 列: code/name/scheme/entry_date/exit_date/entry_price/exit_price/
                 hold_days/exit_reason/return_pct/signal_z/signal_dev
    返回写入行数。按 (code, entry_date, scheme) 覆盖。
    """
    import pandas as pd
    rows = list(zip(
        trade_df['code'].astype(str).str.zfill(6),
        trade_df['name'].astype(str),
        trade_df['scheme'].astype(str),
        trade_df['entry_date'].astype(str),
        trade_df['exit_date'].astype(str),
        pd.to_numeric(trade_df.get('entry_price'), errors='coerce'),
        pd.to_numeric(trade_df.get('exit_price'), errors='coerce'),
        pd.to_numeric(trade_df.get('hold_days'), errors='coerce'),
        trade_df['exit_reason'].astype(str),
        pd.to_numeric(trade_df.get('return_pct'), errors='coerce'),
        pd.to_numeric(trade_df.get('signal_z'), errors='coerce'),
        pd.to_numeric(trade_df.get('signal_dev'), errors='coerce'),
    ))
    with conn:
        conn.executemany("""
            INSERT INTO backtest_trades(code, name, scheme, entry_date, exit_date,
                entry_price, exit_price, hold_days, exit_reason, return_pct,
                signal_z, signal_dev)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code, entry_date, scheme) DO UPDATE SET
                name=excluded.name, exit_date=excluded.exit_date,
                entry_price=excluded.entry_price, exit_price=excluded.exit_price,
                hold_days=excluded.hold_days, exit_reason=excluded.exit_reason,
                return_pct=excluded.return_pct, signal_z=excluded.signal_z,
                signal_dev=excluded.signal_dev
        """, rows)
    return len(rows)


def import_theme(conn, xlsx_path):
    """从 基金规模_含行业主题.xlsx 刷新 funds.theme

    逻辑与旧 app.py 一致：按基金代码取第一个非空 行业主题。
    返回更新的基金数。
    """
    import pandas as pd
    df = pd.read_excel(xlsx_path, dtype={'基金代码': str})
    if '行业主题' not in df.columns:
        raise ValueError(f"{xlsx_path} 中没有 行业主题 列")
    df = df[df['行业主题'].notna() & (df['行业主题'].astype(str) != '')]
    theme_map = df.groupby(df['基金代码'].astype(str).str.zfill(6))['行业主题'].first()
    rows = [(str(theme), code) for code, theme in theme_map.items()]
    with conn:
        conn.executemany("UPDATE funds SET theme = ? WHERE code = ?", rows)
    return len(rows)


# ===================================================================
#  命令行
# ===================================================================

def _print_summary(conn):
    for table in ('funds', 'fund_scale', 'fund_price'):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table}: {n} 行")
    for table in ('fund_scale', 'fund_price'):
        lo, hi = conn.execute(
            f"SELECT MIN(date), MAX(date) FROM {table}").fetchone()
        print(f"{table} 日期范围: {lo} ~ {hi}")
    row = conn.execute(
        "SELECT * FROM v_total_scale ORDER BY date DESC LIMIT 1").fetchone()
    if row:
        print(f"v_total_scale 最新: 日期={row[0]} 总规模={row[1]}亿份 "
              f"总金额={row[2]}亿元 基金数={row[3]} 有价基金数={row[4]}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='ETF 数据库工具')
    parser.add_argument('--import-theme', metavar='XLSX',
                        help='从含行业主题的xlsx刷新 funds.theme')
    args = parser.parse_args()

    conn = connect()
    if args.import_theme:
        n = import_theme(conn, args.import_theme)
        print(f"已更新 {n} 只基金的行业主题")
    else:
        _print_summary(conn)
    conn.close()
