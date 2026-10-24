# -*- coding: utf-8 -*-
"""一次性迁移脚本：基金规模.xlsx + etf_prices.csv -> etf.db

按 CLAUDE.md 数据准确性铁律执行完整交叉验证，任一检查失败以非零退出。

用法：
  python migrate_to_sqlite.py           # 迁移（etf.db 已有数据时拒绝）
  python migrate_to_sqlite.py --force   # 删除现有 etf.db 重新迁移
"""

import os
import sys
import argparse
import random

import pandas as pd

import db

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_FILE = os.path.join(SCRIPT_DIR, '基金规模.xlsx')
THEME_FILE = os.path.join(SCRIPT_DIR, '基金规模_含行业主题.xlsx')
PRICE_CSV = os.path.join(SCRIPT_DIR, 'etf_prices.csv')
TOTAL_CSV = os.path.join(SCRIPT_DIR, 'etf_total_scale.csv')

CHECKS = []  # (名称, 是否通过, 详情)


def check(name, ok, detail=''):
    CHECKS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ''))
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='删除现有 etf.db 重新迁移')
    args = parser.parse_args()

    # -- 0. 数据库存在性检查 --
    if os.path.exists(db.DB_PATH):
        conn0 = db.connect()
        n = conn0.execute("SELECT COUNT(*) FROM fund_scale").fetchone()[0]
        conn0.close()
        if n > 0 and not args.force:
            print(f"[中止] {db.DB_PATH} 已有 {n} 行份额数据。确认要重迁移请加 --force")
            return 2
        if args.force:
            for suffix in ('', '-wal', '-shm'):
                p = db.DB_PATH + suffix
                if os.path.exists(p):
                    os.remove(p)
            print("已删除现有数据库 (--force)")

    # ================= 1. 读取源数据 =================
    print(">>> 读取 基金规模.xlsx （全部 sheet）...")
    xl = pd.ExcelFile(EXCEL_FILE)
    frames = [pd.read_excel(EXCEL_FILE, sheet_name=s, dtype={'基金代码': str})
              for s in xl.sheet_names]
    scale = pd.concat(frames, ignore_index=True)
    raw_scale_rows = len(scale)
    print(f"  原始行数: {raw_scale_rows} (sheets: {xl.sheet_names})")

    scale['日期'] = pd.to_datetime(scale['日期'])
    scale['基金代码'] = scale['基金代码'].astype(str).str.zfill(6)
    scale['基金规模(份)'] = pd.to_numeric(scale['基金规模(份)'], errors='coerce')
    dropped_na = scale['基金规模(份)'].isna().sum()
    scale = scale.dropna(subset=['基金规模(份)'])

    dup_mask = scale.duplicated(subset=['日期', '基金代码'], keep='last')
    dropped_dup = int(dup_mask.sum())
    scale = scale[~dup_mask]
    print(f"  丢弃份额空值: {dropped_na} 行, 丢弃(日期,代码)重复: {dropped_dup} 行, 净: {len(scale)} 行")

    print(">>> 读取 etf_prices.csv ...")
    prices = pd.read_csv(PRICE_CSV, dtype={'基金代码': str})
    raw_price_rows = len(prices)
    prices['基金代码'] = prices['基金代码'].astype(str).str.zfill(6)
    prices['日期'] = pd.to_datetime(prices['日期'])
    prices['收盘价'] = pd.to_numeric(prices['收盘价'], errors='coerce')
    p_na = prices['收盘价'].isna().sum()
    prices = prices.dropna(subset=['收盘价'])
    p_dup = prices.duplicated(subset=['基金代码', '日期'], keep='last').sum()
    prices = prices.drop_duplicates(subset=['基金代码', '日期'], keep='last')
    print(f"  原始: {raw_price_rows} 行, 丢弃空价: {p_na}, 丢弃重复: {p_dup}, 净: {len(prices)} 行")

    # ================= 2. 写入数据库 =================
    conn = db.connect()

    print(">>> 写入 fund_scale ...")
    scale_rows = list(zip(
        scale['日期'].dt.strftime('%Y-%m-%d'),
        scale['基金代码'],
        scale['基金规模(份)'].astype(float),
    ))
    with conn:
        conn.executemany(
            "INSERT INTO fund_scale(date, code, shares) VALUES (?, ?, ?)", scale_rows)
    print(f"  {len(scale_rows)} 行")

    print(">>> 构建 funds 维度表 ...")
    # 每只基金取最新日期行的属性；发行年份取首个非空值
    latest = (scale.sort_values('日期')
                   .groupby('基金代码', as_index=False)
                   .last())
    issue_year = (scale.dropna(subset=['发行年份'])
                       .sort_values('日期')
                       .groupby('基金代码')['发行年份'].first()
                  if '发行年份' in scale.columns else pd.Series(dtype=float))
    fund_rows = []
    def _s(v):
        return str(v) if pd.notna(v) else ''
    for _, r in latest.iterrows():
        iy = issue_year.get(r['基金代码'])
        fund_rows.append((
            r['基金代码'],
            _s(r.get('基金简称')),
            _s(r.get('类型')),
            _s(r.get('市场')),
            int(iy) if pd.notna(iy) else int(r['日期'].year),
        ))
    with conn:
        conn.executemany(
            "INSERT INTO funds(code, name, type, market, issue_year) VALUES (?,?,?,?,?)",
            fund_rows)
    print(f"  {len(fund_rows)} 只基金")

    if os.path.exists(THEME_FILE):
        n_theme = db.import_theme(conn, THEME_FILE)
        print(f"  行业主题: 更新 {n_theme} 只")
    else:
        n_theme = 0
        print("  [跳过] 基金规模_含行业主题.xlsx 不存在")

    print(">>> 写入 fund_price ...")
    n_price = db.upsert_prices(conn, prices)
    print(f"  {n_price} 行")

    # ================= 3. 验证 =================
    print("\n" + "=" * 60)
    print("验证报告（CLAUDE.md 铁律）")
    print("=" * 60)

    # 3.1 行数对账
    n_scale_db = conn.execute("SELECT COUNT(*) FROM fund_scale").fetchone()[0]
    n_price_db = conn.execute("SELECT COUNT(*) FROM fund_price").fetchone()[0]
    n_funds_db = conn.execute("SELECT COUNT(*) FROM funds").fetchone()[0]
    check("fund_scale 行数 == xlsx 清洗后行数",
          n_scale_db == len(scale), f"DB {n_scale_db} vs 源 {len(scale)} (原始 {raw_scale_rows})")
    check("fund_price 行数 == csv 清洗后行数",
          n_price_db == len(prices), f"DB {n_price_db} vs 源 {len(prices)} (原始 {raw_price_rows})")
    check("funds 行数 == 唯一基金数",
          n_funds_db == scale['基金代码'].nunique(),
          f"DB {n_funds_db} vs 源 {scale['基金代码'].nunique()}")

    # 3.2 日期范围
    lo, hi = conn.execute("SELECT MIN(date), MAX(date) FROM fund_scale").fetchone()
    check("fund_scale 日期范围一致",
          lo == scale['日期'].min().strftime('%Y-%m-%d') and
          hi == scale['日期'].max().strftime('%Y-%m-%d'), f"{lo} ~ {hi}")

    # 3.3 逐日份额总和（未舍入，容差 0.005 亿份）
    db_daily = dict(conn.execute("SELECT date, SUM(shares) FROM fund_scale GROUP BY date"))
    src_daily = scale.groupby(scale['日期'].dt.strftime('%Y-%m-%d'))['基金规模(份)'].sum()
    bad_dates = []
    for d, src_sum in src_daily.items():
        diff_yi = abs(db_daily.get(d, 0) - src_sum) / 1e8
        if diff_yi > 0.005:
            bad_dates.append((d, diff_yi))
    check("逐日份额总和 DB == xlsx (容差0.005亿份)",
          not bad_dates and len(db_daily) == len(src_daily),
          f"{len(src_daily)} 个日期全部核对" if not bad_dates else f"不符: {bad_dates[:5]}")

    # 3.4 视图 vs etf_total_scale.csv 全量对比（用未舍入聚合对比，容差 0.006）
    if os.path.exists(TOTAL_CSV):
        old = pd.read_csv(TOTAL_CSV)
        raw_view = pd.read_sql_query("""
            SELECT d.date AS 日期, d.ts AS 总规模_raw, a.ta AS 总金额_raw,
                   d.fc AS 基金数量, a.pc AS 有价基金数
            FROM (SELECT date, SUM(shares)/1e8 AS ts, COUNT(*) AS fc
                  FROM fund_scale WHERE date >= '2025-01-01' GROUP BY date) d
            LEFT JOIN (SELECT s.date, SUM(s.shares*p.close)/1e8 AS ta, COUNT(*) AS pc
                       FROM fund_scale s JOIN fund_price p
                         ON p.date = s.date AND p.code = s.code GROUP BY s.date) a
              ON a.date = d.date
        """, conn)
        merged = old.merge(raw_view, on='日期', how='outer', suffixes=('_csv', ''))
        issues = []
        for _, r in merged.iterrows():
            if pd.isna(r['总规模_raw']) or pd.isna(r['总规模(亿份)']):
                issues.append((r['日期'], '缺行')); continue
            if abs(r['总规模_raw'] - r['总规模(亿份)']) > 0.006:
                issues.append((r['日期'], '总规模'))
            if int(r['基金数量_csv' if '基金数量_csv' in merged.columns else '基金数量']) != int(r['基金数量']):
                issues.append((r['日期'], '基金数量'))
            has_csv_amt = pd.notna(r['总金额(亿元)'])
            has_db_amt = pd.notna(r['总金额_raw'])
            if has_csv_amt != has_db_amt:
                issues.append((r['日期'], '金额有无不一致'))
            elif has_csv_amt and abs(r['总金额_raw'] - r['总金额(亿元)']) > 0.006:
                issues.append((r['日期'], '总金额'))
        check("v_total_scale vs etf_total_scale.csv 全量对比",
              not issues, f"{len(merged)} 行全部核对" if not issues else f"不符: {issues[:8]}")
    else:
        check("etf_total_scale.csv 存在", False, "文件不存在，跳过视图对比")

    # 3.5 随机抽查5个收盘价
    sample = prices.sample(5, random_state=42)
    spot_ok = True
    for _, r in sample.iterrows():
        v = conn.execute("SELECT close FROM fund_price WHERE date=? AND code=?",
                         (r['日期'].strftime('%Y-%m-%d'), r['基金代码'])).fetchone()
        if v is None or abs(v[0] - r['收盘价']) > 1e-9:
            spot_ok = False
    check("随机抽查5个(代码,日期)收盘价", spot_ok)

    # 3.6 代码规范：全部恰好6位
    bad_code = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT code FROM fund_scale WHERE LENGTH(code) != 6
            UNION ALL SELECT code FROM fund_price WHERE LENGTH(code) != 6
            UNION ALL SELECT code FROM funds WHERE LENGTH(code) != 6)
    """).fetchone()[0]
    check("所有基金代码恰好6位", bad_code == 0, f"异常 {bad_code} 条" if bad_code else '')

    # 信息项（不计入通过/失败）
    orphan = conn.execute("""
        SELECT COUNT(DISTINCT code) FROM fund_price
        WHERE code NOT IN (SELECT code FROM funds)
    """).fetchone()[0]
    print(f"  [INFO] 价格表中不在 funds 维度的代码: {orphan} 个")
    print(f"  [INFO] 行业主题覆盖: {n_theme} 只")

    conn.close()

    # ================= 4. 汇总 =================
    failed = [c for c in CHECKS if not c[1]]
    print("\n" + "=" * 60)
    if failed:
        print(f"迁移完成但验证失败 {len(failed)} 项 — 请勿使用该数据库！")
        for name, _, detail in failed:
            print(f"  FAIL: {name} {detail}")
        return 1
    print(f"迁移成功！全部 {len(CHECKS)} 项验证通过。数据库: {db.DB_PATH}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
