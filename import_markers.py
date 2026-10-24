# -*- coding: utf-8 -*-
"""从研究输出 CSV 导入 信号事件 + 回测交易 到 etf.db

按用户需求：买卖点数据先落库，app.py 再从数据库读取展示。

来源（已冻结 2026-08-25 的研究产出，与报告数字一致）：
  data/events/dividend_low_vol_extreme_events.csv   → signal_events（仅 E2）
  data/backtest/backtest_trades.csv                 → backtest_trades

用法：
  F:/anaconda3/python import_markers.py          # 执行导入并验证

新增表（db.py SCHEMA 已定义）：
  signal_events(date, code, event, close, share_z_20, price_dev_20)  PK(date,code)
  backtest_trades(code, name, scheme, entry_date, exit_date, ...)    PK(code,entry_date,scheme)

注意：只写新增信号/交易表，不动 fund_scale / fund_price / funds（CLAUDE.md 数据铁律）。
"""

import os
import sys

import pandas as pd

import db

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EVENTS_CSV = os.path.join(SCRIPT_DIR, 'data', 'events',
                          'dividend_low_vol_extreme_events.csv')
TRADES_CSV = os.path.join(SCRIPT_DIR, 'data', 'backtest',
                          'backtest_trades.csv')

E2_LABEL = 'E2_extreme_share_undervalued'


def main():
    # ================= 1. 读取并清洗 =================
    print(">>> 读取 信号事件 CSV ...")
    sig = pd.read_csv(EVENTS_CSV, encoding='utf-8-sig', dtype={'code': str})
    sig['code'] = sig['code'].astype(str).str.zfill(6)
    sig['date'] = sig['date'].astype(str)
    raw_sig = len(sig)
    e2 = sig[sig['event'] == E2_LABEL].copy()
    print(f"  原始 {raw_sig} 行，E2 信号 {len(e2)} 行")

    print(">>> 读取 回测交易 CSV ...")
    trd = pd.read_csv(TRADES_CSV, encoding='utf-8-sig', dtype={'code': str})
    trd['code'] = trd['code'].astype(str).str.zfill(6)
    print(f"  {len(trd)} 笔交易（方案: {sorted(trd['scheme'].astype(str).unique())}）")

    # ================= 2. 写入数据库 =================
    conn = db.connect()
    n_sig = db.upsert_signal_events(conn, e2)
    n_trd = db.upsert_backtest_trades(conn, trd)
    conn.close()
    print(f">>> 写入 signal_events {n_sig} 行, backtest_trades {n_trd} 行")

    # ================= 3. 验证 =================
    print("\n" + "=" * 60)
    print("验证报告（交叉核对，CLAUDE.md 铁律）")
    print("=" * 60)

    conn = db.connect()
    checks = []

    # 3.1 行数对账
    n_db_sig = conn.execute("SELECT COUNT(*) FROM signal_events").fetchone()[0]
    n_db_trd = conn.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()[0]
    checks.append(("signal_events 行数 == CSV E2 行数",
                   n_db_sig == len(e2), f"DB {n_db_sig} vs CSV {len(e2)}"))
    checks.append(("backtest_trades 行数 == CSV 行数",
                   n_db_trd == len(trd), f"DB {n_db_trd} vs CSV {len(trd)}"))

    # 3.2 全部 E2 信号都在 fund_scale 中存在对应日期的份额记录（买点可标在规模线上）
    missing_scale = conn.execute("""
        SELECT COUNT(*) FROM signal_events s
        LEFT JOIN fund_scale f ON f.date = s.date AND f.code = s.code
        WHERE f.shares IS NULL
    """).fetchone()[0]
    checks.append(("E2 信号日期在 fund_scale 有份额记录",
                   missing_scale == 0, f"缺失 {missing_scale}"))

    # 3.3 全部交易的买卖日在 fund_price 中存在（买/卖点可标在价格线上）
    missing_price = conn.execute("""
        SELECT COUNT(*) FROM backtest_trades t
        LEFT JOIN fund_price p ON p.date = t.entry_date AND p.code = t.code
        WHERE p.close IS NULL
    """).fetchone()[0]
    checks.append(("全部交易 entry_date 在 fund_price 有价格",
                   missing_price == 0, f"缺失 {missing_price}"))

    # 3.4 按基金核对 E2 信号数量（与研究报告的 39 个分布一致）
    rows = conn.execute("""
        SELECT code, COUNT(*) FROM signal_events
        WHERE event = ? GROUP BY code ORDER BY code
    """, (E2_LABEL,)).fetchall()
    detail = ", ".join(f"{c}={n}" for c, n in rows)
    checks.append(("E2 信号按基金分布", len(rows) == 7 and sum(n for _, n in rows) == 39,
                   detail))

    # 3.5 抽查 159307 固定40 交易（与 backtest_trades.csv 报告的日期核对）
    spot = conn.execute("""
        SELECT entry_date, exit_date, exit_reason FROM backtest_trades
        WHERE code='159307' AND scheme='fixed_40' ORDER BY entry_date
    """).fetchall()
    expect = [('2025-04-07', '2025-06-06', 'fixed_40'),
              ('2025-09-18', '2025-11-21', 'fixed_40'),
              ('2025-11-24', '2026-01-21', 'fixed_40'),
              ('2026-05-21', '2026-07-17', 'fixed_40')]
    checks.append(("159307 方案1 交易日期抽查",
                   spot == expect, str(spot)))

    # ================= 4. 汇总 =================
    failed = [c for c in checks if not c[1]]
    ok = [c for c in checks if c[1]]
    for name, _, detail in ok:
        print(f"  [PASS] {name} — {detail}")
    for name, _, detail in failed:
        print(f"  [FAIL] {name} — {detail}")
    conn.close()

    if failed:
        print(f"\n导入完成但验证失败 {len(failed)} 项，请检查！")
        return 1
    print(f"\n导入成功！全部 {len(checks)} 项验证通过，数据可用。")
    return 0


if __name__ == '__main__':
    sys.exit(main())