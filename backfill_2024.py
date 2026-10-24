# -*- coding: utf-8 -*-
"""回填 2024 年 ETF 份额数据

复用 daily_update.py 的抓取/标准化逻辑，逐日回填 2024-01-02 ~ 2024-12-31
的所有交易日份额数据。节假日/周末由抓取返回空数据自动跳过。

用法：
  F:/anaconda3/python backfill_2024.py            # 正式写入
  F:/anaconda3/python backfill_2024.py --dry-run  # 预览，不写入
  F:/anaconda3/python backfill_2024.py --start 20240102 --end 20241231
"""

import argparse
import sys
import os
import time
from datetime import timedelta

import pandas as pd

import db

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import daily_update as du


def trading_days(start, end):
    """生成 start~end 之间所有工作日（YYYYMMDD 升序）；节假日由抓取跳过"""
    days = []
    d = pd.to_datetime(start)
    end_dt = pd.to_datetime(end)
    while d <= end_dt:
        if d.weekday() < 5:  # 周一~周五
            days.append(d.strftime('%Y%m%d'))
        d += timedelta(days=1)
    return days


def main():
    parser = argparse.ArgumentParser(description='回填 2024 年 ETF 规模数据')
    parser.add_argument('--start', default='20240102')
    parser.add_argument('--end', default='20241231')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    days = trading_days(args.start, args.end)
    print(f"共 {len(days)} 个工作日待抓取 ({args.start} ~ {args.end})")

    conn = db.connect()
    updated, skipped, failed, api_fail = [], [], [], []

    for i, d in enumerate(days):
        day_df, status = du.fetch_one_day_scale(d)
        if status == 'ok':
            n_sh = len(day_df[day_df['市场'] == '上海'])
            n_sz = len(day_df[day_df['市场'] == '深圳'])
            yi = day_df['基金规模(份)'].sum() / 100_000_000
            if args.dry_run:
                print(f"  [{d}] OK 沪{n_sh}/深{n_sz} 总{yi:.2f}亿份 [DRY-RUN]")
                updated.append(d)
            else:
                db.replace_scale_for_date(conn, d, day_df)
                db_sum = conn.execute(
                    "SELECT SUM(shares) FROM fund_scale WHERE date = ?",
                    (db.norm_date(d),)).fetchone()[0] or 0
                db_yi = db_sum / 100_000_000
                if abs(db_yi - yi) > 0.005:
                    print(f"  [{d}] [FAIL] 核对不符 DB{db_yi:.2f} vs 抓取{yi:.2f}")
                    failed.append(d)
                    conn.execute("DELETE FROM fund_scale WHERE date = ?",
                                 (db.norm_date(d),))
                    conn.commit()
                else:
                    print(f"  [{d}] OK 沪{n_sh}/深{n_sz} 写入核对一致 {db_yi:.2f}亿份")
                    updated.append(d)
        elif status == 'empty':
            skipped.append(d)
            print(f"  [{d}] 无数据（节假日）")
        else:
            api_fail.append(d)
            print(f"  [{d}] [FAIL] 接口失败")

        if i < len(days) - 1:
            time.sleep(0.5)

    conn.close()
    print("=" * 60)
    print(f"成功: {len(updated)}  节假日跳过: {len(skipped)}  "
          f"核对失败: {len(failed)}  接口失败: {len(api_fail)}")
    if api_fail:
        print(f"接口失败日期: {api_fail}")
    if failed:
        print(f"核对失败日期: {failed}")


if __name__ == '__main__':
    sys.exit(main())