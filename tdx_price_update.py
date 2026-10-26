# -*- coding: utf-8 -*-
"""从通达信本地 .day 文件提取 ETF 收盘价，更新 etf.db (fund_price)

用法：
  F:/anaconda3/python tdx_price_update.py           # 增量更新（数据库最新日期之后）
  F:/anaconda3/python tdx_price_update.py --all     # 提取全部历史价格
  F:/anaconda3/python tdx_price_update.py --date 20260629  # 提取指定日期之后

通达信 .day 文件格式（每条记录 32 字节）：
  uint32 date    - YYYYMMDD (little-endian)
  uint32 open    - 开盘价 × 1000
  uint32 high    - 最高价 × 1000
  uint32 low     - 最低价 × 1000
  uint32 close   - 收盘价 × 1000
  float  amount  - 成交额（元）
  uint32 volume  - 成交量（手/股）
  uint32 reserved
"""

import sys
import os
import struct
import time
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd

import db

# -- 配置 --
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TDX_BASE = r'D:\tdx\vipdoc'

# 大盘指数代码 + 中文简称（存入 fund_price 时 code 使用纯数字代码）
MARKET_INDICES = {
    '000300': '沪深300',
    '000510': '中证A500',
    '000016': '上证50',
    '000852': '中证1000',
    '000001': '上证综指',
    '399001': '深证成指',
}

RECORD_SIZE = 32
RECORD_FMT = '<IIIIIfII'  # little-endian: date, open, high, low, close, amount, volume, reserved


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


def get_etf_codes(conn):
    """从 etf.db funds 表读取所有 ETF 代码"""
    codes = db.fund_codes(conn)
    log(f"读取到 {len(codes)} 只 ETF 代码")
    return codes


def code_to_day_path(code):
    """将代码映射到通达信 .day 文件路径 (ETF + 指数)"""
    # 大盘指数
    if code in MARKET_INDICES:
        if code in ('000300', '000510', '000016', '000852', '000001'):
            return os.path.join(TDX_BASE, 'sh', 'lday', f'sh{code}.day')
        elif code == '399001':
            return os.path.join(TDX_BASE, 'sz', 'lday', f'sz{code}.day')
    # 上海 ETF：本地通达信文件均位于 sh/lday，涵盖 51/52/53/55/56/58 段。
    # 52、53、55 段已核对存在于本机 .day 文件目录；58 已包含 589 段。
    if code.startswith(('51', '52', '53', '55', '56', '58')):
        return os.path.join(TDX_BASE, 'sh', 'lday', f'sh{code}.day')
    elif code.startswith(('15', '16', '18')):
        return os.path.join(TDX_BASE, 'sz', 'lday', f'sz{code}.day')
    return None


def parse_day_file(filepath):
    """解析 .day 文件，返回 DataFrame (date, close_price, volume)"""
    if not os.path.exists(filepath):
        return None

    with open(filepath, 'rb') as f:
        data = f.read()

    records = []
    for i in range(0, len(data), RECORD_SIZE):
        if i + RECORD_SIZE > len(data):
            break
        try:
            date, op, hi, lo, cl, amount, vol, reserved = struct.unpack_from(RECORD_FMT, data, i)
            # 验证日期的合理性
            if date < 19900101 or date > 20991231:
                continue
            if cl == 0:
                continue
            close_price = cl / 1000.0
            # 过滤异常值（ETF价格一般在0.1-100之间）
            if 0.01 <= close_price <= 1000:
                records.append({
                    '日期': pd.to_datetime(str(date), format='%Y%m%d'),
                    '收盘价': close_price,
                    '成交量': vol,  # 单位：手（1手=100股）
                })
        except Exception:
            continue

    if not records:
        return None

    df = pd.DataFrame(records)
    df = df.sort_values('日期').drop_duplicates(subset='日期', keep='last')
    return df


def update_prices_from_tdx(target_date=None, all_history=False, include_indices=False):
    """从通达信提取 ETF（和指数）价格+成交量并更新 etf.db (fund_price)"""
    conn = db.connect()
    codes = get_etf_codes(conn)

    if include_indices:
        idx_codes = list(MARKET_INDICES.keys())
        codes.extend(idx_codes)
        log(f"同时提取 {len(idx_codes)} 只大盘指数")

    n_existing = conn.execute("SELECT COUNT(*) FROM fund_price").fetchone()[0]
    log(f"现有价格数据: {n_existing} 行, 最新日期: {db.max_price_date(conn)}")

    if target_date is None and not all_history:
        # 默认：增量更新（数据库最新日期之后，不覆盖已有数据）
        target_dt = pd.to_datetime(db.max_price_date(conn)) if db.max_price_date(conn) else pd.to_datetime('20200101')
        log(f"模式: 增量更新（寻找 {target_dt.strftime('%Y-%m-%d')} 之后的新数据）")
    elif all_history:
        log("模式: 全量提取历史价格+成交量")
        target_dt = pd.to_datetime('19000101')
    else:
        target_dt = pd.to_datetime(target_date)
        log(f"模式: 提取 {target_dt.strftime('%Y-%m-%d')} 之后的数据")

    # 增量模式：按每只基金各自的最新日期过滤（可自动补齐个别基金落后的缺口）
    max_by_code = db.price_max_date_by_code(conn) if not all_history else {}

    found = 0
    missing = 0
    no_new = 0
    total_parsed = 0  # 累计解析行数

    for i, code in enumerate(codes):
        day_path = code_to_day_path(code)
        if day_path is None:
            missing += 1
            continue

        df = parse_day_file(day_path)
        if df is None or len(df) == 0:
            missing += 1
            if missing <= 4:
                log(f"  [SKIP] {code} 无 .day 文件或解析失败")
            continue

        if not all_history:
            if target_date is None and code in max_by_code:
                # 增量：取该基金自身最新日期之后的数据
                cutoff = pd.to_datetime(max_by_code[code])
            else:
                cutoff = target_dt
            df = df[df['日期'] > cutoff]

        if len(df) == 0:
            no_new += 1
            found += 1
        else:
            df['基金代码'] = code
            n = db.upsert_prices(conn, df)
            total_parsed += len(df)
            found += 1
            if (i + 1) % 500 == 0 or (i + 1) == len(codes):
                log(f"  进度: {i+1}/{len(codes)}, 已解析 {total_parsed} 行")

    log(f"  处理完成: 有数据 {found}, 无新数据 {no_new}, 缺失 {missing}")

    if total_parsed == 0:
        log("没有新数据需要更新！")
        conn.close()
        return


    # 统计最新数据
    max_date = db.max_price_date(conn)
    n_latest = conn.execute(
        "SELECT COUNT(*) FROM fund_price WHERE date = ?", (max_date,)).fetchone()[0]
    log(f"  最新日期: {max_date}, 覆盖 {n_latest} 只基金")
    n_total = conn.execute("SELECT COUNT(*) FROM fund_price").fetchone()[0]
    n_codes = conn.execute("SELECT COUNT(DISTINCT code) FROM fund_price").fetchone()[0]
    log(f"  总计: {n_total} 行, {n_codes} 只基金")
    conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='从通达信本地数据提取ETF/指数价格+成交量')
    parser.add_argument('--date', type=str, default='', help='提取指定日期 YYYYMMDD')
    parser.add_argument('--all', action='store_true', help='提取全部历史价格+成交量')
    parser.add_argument('--index', action='store_true', help='同时提取大盘指数价格+成交量')
    parser.add_argument('--from-date', type=str, default='',
                        help='仅提取此日期之后的数据 (YYYYMMDD, 默认 20190101)')
    args = parser.parse_args()

    start = time.time()
    log("=" * 60)
    log("价格+成交量提取（数据来源：通达信本地 .day 文件）")
    log("=" * 60)

    # 处理 from-date
    target_date = None
    if args.date:
        target_date = args.date
    elif args.from_date:
        target_date = args.from_date  # 覆盖默认起始日期
        log(f"起始日期限制: {target_date}")

    update_prices_from_tdx(
        target_date=target_date if target_date else None,
        all_history=args.all,
        include_indices=args.index,
    )

    elapsed = time.time() - start
    log(f"完成! 耗时 {elapsed:.0f} 秒 ({elapsed/60:.1f} 分钟)")


if __name__ == '__main__':
    main()
