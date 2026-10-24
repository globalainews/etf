# -*- coding: utf-8 -*-
"""批量抓取ETF基金每日收盘价数据（来源：东方财富，通过akshare）

用法：
  python fetch_prices.py              # 全量抓取
  python fetch_prices.py --update     # 增量更新（只抓新日期）
  python fetch_prices.py --resume     # 从中断点恢复
  python fetch_prices.py --codes 159001,510050  # 只抓指定代码
"""
import sys
import os
import time
import json
import argparse
import traceback
from datetime import datetime, timedelta

import pandas as pd
import akshare as ak

# ── 配置 ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_FILE = os.path.join(SCRIPT_DIR, '基金规模.xlsx')
PRICE_CSV = os.path.join(SCRIPT_DIR, 'etf_prices.csv')
CHECKPOINT_FILE = os.path.join(SCRIPT_DIR, 'fetch_checkpoint.json')
START_DATE = '20250102'
REQUEST_DELAY = 0.35       # 每次API调用间隔（秒）
BATCH_SAVE = 50            # 每N只基金保存断点
MAX_RETRIES = 3            # 单只基金最大重试次数

# ── 工具函数 ──
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def get_end_date():
    """返回昨天日期（交易日可能不准确，但akshare会自动处理）"""
    return (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')


def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def save_checkpoint(codes_ordered, completed, failed, current_idx, total_rows):
    data = {
        'last_completed_index': current_idx,
        'fund_codes_ordered': codes_ordered,
        'completed_codes': completed,
        'failed_codes': failed,
        'total_fetched_rows': total_rows,
        'last_updated': datetime.now().isoformat()
    }
    with open(CHECKPOINT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_existing_prices():
    """加载已有价格数据，返回DataFrame或None"""
    if os.path.exists(PRICE_CSV):
        try:
            df = pd.read_csv(PRICE_CSV, dtype={'基金代码': str})
            df['日期'] = pd.to_datetime(df['日期'])
            log(f"已加载现有价格数据: {len(df)}行, {df['基金代码'].nunique()}只基金")
            return df
        except Exception:
            log("现有价格文件损坏，将重新抓取")
    return None


def get_fund_codes():
    """从Excel读取基金代码列表（按最新规模降序排列）"""
    xl = pd.ExcelFile(EXCEL_FILE)
    dfs = [pd.read_excel(EXCEL_FILE, sheet_name=s, dtype={'基金代码': str}) for s in xl.sheet_names]
    df = pd.concat(dfs, ignore_index=True)
    df['日期'] = pd.to_datetime(df['日期'])
    # 按最新日期、规模降序
    latest = df[df['日期'] == df['日期'].max()]
    latest = latest.sort_values('基金规模(份)', ascending=False)
    codes = latest['基金代码'].unique().tolist()
    log(f"从Excel读取到 {len(codes)} 只基金代码")
    return codes


def fetch_one_etf(code, start_date, end_date):
    """
    抓取单只ETF的历史收盘价。
    成功返回 DataFrame（列: 日期, 收盘价），失败返回 None。
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            df = ak.fund_etf_hist_em(
                symbol=code,
                period='daily',
                start_date=start_date,
                end_date=end_date,
                adjust='qfq'
            )
            if df is None or len(df) == 0:
                return None  # 无数据，不重试

            result = df[['日期', '收盘']].copy()
            result.columns = ['日期', '收盘价']
            result.insert(0, '基金代码', code)
            return result

        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                log(f"  {code} 第{attempt}次失败，{wait}秒后重试: {e}")
                time.sleep(wait)
            else:
                log(f"  {code} 重试{MAX_RETRIES}次后仍失败: {e}")
                return None


def run_fetch(codes, start_date, end_date, existing_prices=None, resume_from=0):
    """
    主抓取循环。
    existing_prices: 已有价格DataFrame（用于增量模式）
    resume_from: 从第几个code开始（用于断点续传）
    返回: (completed, failed, total_rows)
    """
    completed = []
    failed = []
    total_rows = len(existing_prices) if existing_prices is not None else 0
    all_new_data = []

    # 确定每个code的start_date
    code_start_dates = {}
    if existing_prices is not None:
        for code in codes:
            code_prices = existing_prices[existing_prices['基金代码'] == code]
            if len(code_prices) > 0:
                last_date = code_prices['日期'].max()
                next_date = (pd.to_datetime(last_date) + timedelta(days=1)).strftime('%Y%m%d')
                code_start_dates[code] = next_date
            else:
                code_start_dates[code] = start_date
    else:
        code_start_dates = {c: start_date for c in codes}

    total_codes = len(codes)
    for i in range(resume_from, total_codes):
        code = codes[i]
        c_start = code_start_dates.get(code, start_date)

        # 如果start_date > end_date，说明已是最新，跳过
        if c_start > end_date:
            completed.append(code)
            continue

        log(f"[{i+1}/{total_codes}] 正在抓取 {code} ({c_start}~{end_date})...")
        df = fetch_one_etf(code, c_start, end_date)

        if df is not None and len(df) > 0:
            all_new_data.append(df)
            total_rows += len(df)
            completed.append(code)
            log(f"  OK {code} 获取 {len(df)} 条记录")
        else:
            failed.append(code)
            log(f"  FAIL {code} 无数据或失败")

        # 定期保存
        if len(all_new_data) >= BATCH_SAVE * 10 or (i + 1) % BATCH_SAVE == 0:
            if all_new_data:
                merged = pd.concat(all_new_data, ignore_index=True)
                merged.to_csv(PRICE_CSV, index=False, encoding='utf-8-sig',
                             mode='a', header=not os.path.exists(PRICE_CSV))
                log(f"  已保存 {len(merged)} 行到 {PRICE_CSV}")
                all_new_data = []
            save_checkpoint(codes, completed, failed, i, total_rows)

        time.sleep(REQUEST_DELAY)

    # 最后保存
    if all_new_data:
        merged = pd.concat(all_new_data, ignore_index=True)
        merged.to_csv(PRICE_CSV, index=False, encoding='utf-8-sig',
                     mode='a', header=not os.path.exists(PRICE_CSV))
        log(f"  最终保存 {len(merged)} 行到 {PRICE_CSV}")

    save_checkpoint(codes, completed, failed, total_codes - 1, total_rows)
    return completed, failed, total_rows


def main():
    parser = argparse.ArgumentParser(description='ETF收盘价批量抓取')
    parser.add_argument('--update', action='store_true', help='增量更新模式')
    parser.add_argument('--resume', action='store_true', help='从中断点恢复')
    parser.add_argument('--codes', type=str, default='', help='指定基金代码（逗号分隔）')
    parser.add_argument('--start', type=str, default=START_DATE, help='起始日期 YYYYMMDD')
    parser.add_argument('--end', type=str, default='', help='结束日期 YYYYMMDD（默认昨天）')
    args = parser.parse_args()

    end_date = args.end if args.end else get_end_date()
    log("ETF收盘价抓取工具")
    log(f"日期范围: {args.start} ~ {end_date}")

    # 获取基金代码
    if args.codes:
        codes = [c.strip() for c in args.codes.split(',') if c.strip()]
        log(f"指定代码: {len(codes)}只")
    else:
        codes = get_fund_codes()

    # 处理存量数据
    existing_prices = load_existing_prices() if args.update else None

    # 断点续传
    resume_from = 0
    if args.resume:
        cp = load_checkpoint()
        if cp:
            resume_from = cp.get('last_completed_index', 0) + 1
            completed = cp.get('completed_codes', [])
            log(f"从断点恢复: 已完成 {len(completed)}只, 从第{resume_from+1}只继续")

    # 如果是增量+全量都没跑过，先删除空文件
    if args.update and not os.path.exists(PRICE_CSV):
        log("增量模式但价格文件不存在，切换到全量模式")
        existing_prices = None

    # 开始抓取
    log(f"开始抓取 {len(codes)} 只基金...")
    start_time = time.time()
    completed, failed, total_rows = run_fetch(
        codes, args.start, end_date,
        existing_prices=existing_prices,
        resume_from=resume_from
    )

    elapsed = time.time() - start_time
    log("="*50)
    log(f"抓取完成! 耗时 {elapsed:.0f}秒 ({elapsed/60:.1f}分钟)")
    log(f"成功: {len(completed)}只 | 失败: {len(failed)}只 | 总行数: {total_rows}")

    # 最终去重（防止追加模式下产生重复）
    if os.path.exists(PRICE_CSV):
        df = pd.read_csv(PRICE_CSV, dtype={'基金代码': str})
        before = len(df)
        df = df.drop_duplicates(subset=['基金代码', '日期'], keep='first')
        after = len(df)
        if before != after:
            df.to_csv(PRICE_CSV, index=False, encoding='utf-8-sig')
            log(f"已去重: {before} -> {after} 行 (移除 {before - after} 条重复)")

    log(f"价格文件: {PRICE_CSV}")

    if failed:
        log(f"失败代码: {', '.join(failed[:20])}{'...' if len(failed) > 20 else ''}")

    # 清理断点文件
    if os.path.exists(CHECKPOINT_FILE) and not failed:
        os.remove(CHECKPOINT_FILE)
        log("所有基金抓取成功，已清除断点文件")


if __name__ == '__main__':
    main()
