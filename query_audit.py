# -*- coding: utf-8 -*-
"""Generate database audit report"""
import sqlite3, datetime, os, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

c = sqlite3.connect('etf.db')

md_lines = []
md_lines.append(f"# Database Audit Report\n")
md_lines.append(f"**Generated**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n")
md_lines.append(f"**Backup**: [etf.db](./etf.db)  \n")
md_lines.append(f"---\n\n")

# 1. Schema
md_lines.append("## Schema\n")
for row in c.execute("PRAGMA table_info(fund_price)").fetchall():
    md_lines.append(f"  - `fund_price.{row[1]}`: {row[2]} {'PK' if row[5] else ''} {'NOT NULL' if row[3] else ''}\n")
for tbl in ['funds', 'fund_scale', 'signal_events', 'backtest_trades']:
    rows = c.execute(f"PRAGMA table_info({tbl})").fetchall()
    if rows:
        md_lines.append(f"\n### `{tbl}`\n")
        for row in rows:
            md_lines.append(f"  - `{row[1]}`: {row[2]} {'PK' if row[5] else ''} {'NOT NULL' if row[3] else ''}\n")

# 2. Table stats
md_lines.append("## Table Statistics\n")
for tbl in ['funds', 'fund_scale', 'fund_price', 'signal_events', 'backtest_trades']:
    n = c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    lo = c.execute(f"SELECT MIN(date) FROM {tbl}").fetchone()[0] if tbl in ('fund_scale', 'fund_price', 'signal_events') else 'N/A'
    hi = c.execute(f"SELECT MAX(date) FROM {tbl}").fetchone()[0] if tbl in ('fund_scale', 'fund_price', 'signal_events') else 'N/A'
    codes = c.execute(f"SELECT COUNT(DISTINCT code) FROM {tbl}").fetchone()[0] if 'code' in tbl else 'N/A'

    vol_stats = ""
    if tbl == 'fund_price':
        vol_not_null = c.execute(f"SELECT COUNT(*) FROM {tbl} WHERE volume IS NOT NULL").fetchone()[0]
        vol_pct = round(vol_not_null * 100.0 / n, 1) if n > 0 else 0
        vol_stats = f", {vol_not_null:,d} ({vol_pct}%) with volume"

    md_lines.append(f"### `{tbl}`\n")
    md_lines.append(f"- Rows: {n:,d}\n")
    if lo != 'N/A':
        md_lines.append(f"- Date range: {lo} ~ {hi}\n")
    if codes != 'N/A':
        md_lines.append(f"- Distinct codes: {codes:,d}\n")
    if vol_stats:
        md_lines.append(f"- Volume: {vol_stats}\n")

# 3. fund_price by code
md_lines.append("\n## fund_price Coverage by Code Type\n")

# Index codes
idx_codes = ['000300', '000510', '000016', '000852', '000001', '399001']
md_lines.append("### 大盘指数\n")
for code in idx_codes:
    r = c.execute('SELECT MIN(date), MAX(date), COUNT(*), ROUND(COUNT(volume)*100.0/COUNT(*),1) FROM fund_price WHERE code=?', (code,)).fetchone()
    md_lines.append(f"- {code}: [{r[0]},{r[1]}] n={r[2]:,d} vol%={r[3]}%\n")

# ETF codes - group by prefix
md_lines.append("\n### ETF (按前缀分组)\n")
prefix_groups = {
    '51xxxx (上交所)': lambda c: c.startswith('51'),
    '56xxxx': lambda c: c.startswith('56'),
    '58xxxx': lambda c: c.startswith('58'),
    '588xxx': lambda c: c.startswith('588'),
    '15xxxx (深交所)': lambda c: c.startswith('15'),
    '16xxxx': lambda c: c.startswith('16'),
    '18xxxx': lambda c: c.startswith('18'),
}
for label, pred in prefix_groups.items():
    codes = [r[0] for r in c.execute("SELECT code FROM fund_price GROUP BY code").fetchall() if pred(r[0])]
    if codes:
        min_dt = c.execute(f"SELECT MIN(date) FROM fund_price WHERE code IN ({','.join(['?' for _ in codes])})", codes).fetchone()[0]
        max_dt = c.execute(f"SELECT MAX(date) FROM fund_price WHERE code IN ({','.join(['?' for _ in codes])})", codes).fetchone()[0]
        total = sum(c.execute(f"SELECT COUNT(*) FROM fund_price WHERE code=?", [code]).fetchone()[0] for code in codes)
        vol_count = sum(c.execute(f"SELECT COUNT(*) FROM fund_price WHERE code=? AND volume IS NOT NULL", [code]).fetchone()[0] for code in codes)
        vol_pct = round(vol_count * 100.0 / total, 1) if total > 0 else 0
        md_lines.append(f"- {label}: {len(codes)} 只, [{min_dt},{max_dt}] 共 {total:,d} 行, {vol_count:,d} 行有成交量 ({vol_pct}%)\n")

# 4. Target ETFs
md_lines.append("\n## 目标 7 只 ETF (2019+)\n")
etf_codes = ['512890', '512000', '512880', '513180', '513130', '515120', '159740']
md_lines.append("| code | 日期范围 | 价格行数 | 有成交量 | 成交量% |\n")
md_lines.append("|------|----------|----------|----------|--------|\n")
for code in etf_codes:
    r = c.execute("SELECT MIN(date), MAX(date), COUNT(*), COUNT(volume) FROM fund_price WHERE date>='2019-01-01' AND code=?", (code,)).fetchone()
    vol_pct = round(r[3] * 100.0 / r[2], 1) if r[2] > 0 else 0
    md_lines.append(f"| {code} | [{r[0]},{r[1]}] | {r[2]:,d} | {r[3]:,d} | {vol_pct}% |\n")

# 5. fund_scale stats
md_lines.append("\n## fund_scale 最新一期\n")
row = c.execute("SELECT s.date, COUNT(*), SUM(s.shares)/1e8, SUM(s.shares*p.close)/1e8 FROM fund_scale s LEFT JOIN fund_price p ON p.date=s.date AND p.code=s.code GROUP BY s.date ORDER BY s.date DESC LIMIT 1").fetchone()
md_lines.append(f"- 日期: {row[0]}\n")
md_lines.append(f"- 基金数: {row[1]:,d}\n")
md_lines.append(f"- 总份额: {row[2]:.2f} 亿份\n")
md_lines.append(f"- 总金额: {row[3]:.2f} 亿元\n")

c.close()

# Write markdown
result = ''.join(md_lines)
print(result)

with open('database_audit.md', 'w', encoding='utf-8') as f:
    f.write(result)

print("\n-> database_audit.md written")
