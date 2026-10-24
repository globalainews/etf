# -*- coding: utf-8 -*-
"""ETF 每日数据更新脚本（数据源: etf.db SQLite）

从上海交易所和深圳交易所获取 ETF 规模数据，并更新价格数据。
不指定 --date 时自动检测缺失日期并全部补齐（最后日期前30天窗口内的
中间缺口 + 最后日期之后到昨天的新日期），节假日空数据自动跳过。

用法：
  python daily_update.py                        # 自动补齐所有缺失日期（含昨日）
  python daily_update.py --date 20260629        # 仅更新指定日期
  python daily_update.py --scale-only           # 仅更新规模数据
  python daily_update.py --price-only           # 仅更新价格数据
  python daily_update.py --dry-run              # 预览模式，不写入

数据来源：
  - 上交所 ETF 规模: ak.fund_etf_scale_sse()  ->  https://www.sse.com.cn/
  - 深交所 ETF 规模: ak.fund_scale_daily_szse()  ->  https://www.szse.cn/
  - ETF 价格行情:   ak.fund_etf_hist_em()      ->  东方财富 (EastMoney)
"""

import sys
import os
import time
import argparse
import traceback
from datetime import datetime, timedelta

import pandas as pd
import akshare as ak

import db

# -- 路径配置 --
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, 'update_log.txt')

# -- 请求配置 --
MAX_RETRIES = 3
REQUEST_DELAY = 0.5


def log(msg):
    """同时输出到控制台和日志文件"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{timestamp}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode('ascii', errors='replace').decode('ascii'), flush=True)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def get_target_date(arg_date):
    """解析目标日期，默认昨天"""
    if arg_date:
        return arg_date
    return (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')


def detect_missing_dates(existing_dates, end_date_str, lookback_days=30):
    """找出需要补的日期：缺失的工作日列表（YYYYMMDD，升序）

    existing_dates: 数据库中已有日期列表（'YYYY-MM-DD'）
    扫描范围：从（现有最后日期 - lookback_days）到 end_date。
    覆盖两类缺口：
      1. 最后日期之后到 end_date 的新日期
      2. 最后日期之前 lookback_days 内的中间缺口（如漏跑了几天后又更新了最新一天）
    周末直接排除；节假日会被扫到，但抓取时交易所返回空数据会自动跳过。
    """
    end_dt = pd.to_datetime(end_date_str).normalize()
    if not existing_dates:
        return [end_dt.strftime('%Y%m%d')]

    have = set(pd.to_datetime(existing_dates))
    last_dt = max(have)
    d = (last_dt - timedelta(days=lookback_days)).normalize()

    missing = []
    while d <= end_dt:
        if d.weekday() < 5 and d not in have:
            missing.append(d.strftime('%Y%m%d'))
        d += timedelta(days=1)
    return missing


# ===================================================================
#  规模数据更新
# ===================================================================

def fetch_sse_scale(date_str):
    """从上交所获取 ETF 规模数据

    返回: DataFrame（空 DataFrame 表示当日无数据）；None 表示接口失败
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log(f"  上交所 API: fund_etf_scale_sse(date={date_str})")
            df = ak.fund_etf_scale_sse(date=date_str)
            if df is None or len(df) == 0:
                log(f"  上交所返回空数据")
                return pd.DataFrame()
            log(f"  上交所返回 {len(df)} 条记录")
            return df
        except Exception as e:
            # akshare 在该日无数据时不返回空表，而是抛列名异常
            # （"None of [Index([...])] are in the [columns]"），视为无数据
            if 'are in the [columns]' in str(e):
                log(f"  上交所无当日数据 (接口空响应)")
                return pd.DataFrame()
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                log(f"  上交所 API 重试 {attempt}/{MAX_RETRIES}，{wait}s 后... ({e})")
                time.sleep(wait)
            else:
                log(f"  [FAIL] 上交所 API 最终失败: {e}")
                return None


def fetch_szse_scale(date_str):
    """从深交所获取 ETF 规模数据

    返回: DataFrame（空 DataFrame 表示当日无数据）；None 表示接口失败
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log(f"  深交所 API: fund_scale_daily_szse(start={date_str}, end={date_str})")
            df = ak.fund_scale_daily_szse(
                start_date=date_str,
                end_date=date_str,
                symbol="ETF"
            )
            if df is None or len(df) == 0:
                # snapshot 接口返回的是"当前"份额，只有目标日期是最近1个自然日时
                # 才能近似当作该日数据；回填历史日期时禁用，避免写入错误数据
                days_ago = (datetime.now() - datetime.strptime(date_str, '%Y%m%d')).days
                if days_ago > 1:
                    log(f"  深交所历史接口返回空（目标日期较早，不使用 snapshot 兜底）")
                    return pd.DataFrame()
                log(f"  深交所历史接口返回空，尝试 snapshot 接口...")
                df = ak.fund_etf_scale_szse()
                if df is not None and len(df) > 0:
                    log(f"  深交所 snapshot 返回 {len(df)} 条")
                    df['日期'] = pd.to_datetime(date_str)
                    df = df.rename(columns={'基金类别': 'ETF类型'})
                    return df
                log(f"  深交所 snapshot 也返回空")
                return pd.DataFrame()
            log(f"  深交所返回 {len(df)} 条记录")
            return df
        except Exception as e:
            if 'are in the [columns]' in str(e):
                log(f"  深交所无当日数据 (接口空响应)")
                return pd.DataFrame()
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                log(f"  深交所 API 重试 {attempt}/{MAX_RETRIES}，{wait}s 后... ({e})")
                time.sleep(wait)
            else:
                log(f"  [FAIL] 深交所 API 最终失败: {e}")
                return None


def normalize_sse_data(raw_df, target_date):
    """将上交所原始数据标准化为统一格式"""
    df = raw_df.copy()
    df['基金代码'] = df['基金代码'].astype(str).str.zfill(6)
    df['市场'] = '上海'
    df['日期'] = pd.to_datetime(target_date)
    df = df.rename(columns={
        'ETF类型': '类型',
        '基金份额': '基金规模(份)',
    })
    cols = ['日期', '基金代码', '基金简称', '类型', '基金规模(份)', '市场']
    df = df[[c for c in cols if c in df.columns]]
    df['基金规模(份)'] = pd.to_numeric(df['基金规模(份)'], errors='coerce')
    return df


def normalize_szse_data(raw_df, target_date, is_hist=True):
    """将深交所原始数据标准化为统一格式"""
    df = raw_df.copy()
    df['基金代码'] = df['基金代码'].astype(str).str.zfill(6)
    df['市场'] = '深圳'

    if is_hist and '日期' in df.columns:
        df['日期'] = pd.to_datetime(df['日期'])
    else:
        df['日期'] = pd.to_datetime(target_date)

    type_col = 'ETF类型' if 'ETF类型' in df.columns else ('基金类别' if '基金类别' in df.columns else None)
    rename_map = {'基金份额': '基金规模(份)'}
    if type_col:
        rename_map[type_col] = '类型'
    df = df.rename(columns=rename_map)

    if '类型' not in df.columns:
        df['类型'] = ''

    cols = ['日期', '基金代码', '基金简称', '类型', '基金规模(份)', '市场']
    df = df[[c for c in cols if c in df.columns]]
    df['基金规模(份)'] = pd.to_numeric(df['基金规模(份)'], errors='coerce')
    return df


def fetch_one_day_scale(target_date):
    """抓取单日上交所+深交所规模数据并标准化

    返回: (DataFrame 或 None, status)
      status: 'ok'    两所数据齐全
              'empty' 两所均返回空（节假日或数据未发布），跳过即可
              'fail'  接口失败或仅单所有数据（写入单所数据会污染当日总量，视为失败）
    """
    log(f">>> [{target_date}] 获取上交所 ETF 规模数据...")
    sse_raw = fetch_sse_scale(target_date)
    sse_df = None
    if sse_raw is None:
        log("  [WARN] 上交所数据获取失败")
    elif len(sse_raw) == 0:
        log("  上交所当日无数据")
    else:
        sse_df = normalize_sse_data(sse_raw, target_date)
        log(f"  上交所标准化: {len(sse_df)} 只基金")

    time.sleep(REQUEST_DELAY)

    log(f">>> [{target_date}] 获取深交所 ETF 规模数据...")
    szse_raw = fetch_szse_scale(target_date)
    szse_df = None
    if szse_raw is None:
        log("  [WARN] 深交所数据获取失败")
    elif len(szse_raw) == 0:
        log("  深交所当日无数据")
    else:
        is_hist = '日期' in szse_raw.columns
        szse_df = normalize_szse_data(szse_raw, target_date, is_hist=is_hist)
        log(f"  深交所标准化: {len(szse_df)} 只基金")

    if sse_df is not None and szse_df is not None:
        return pd.concat([sse_df, szse_df], ignore_index=True), 'ok'
    if sse_raw is not None and len(sse_raw) == 0 and szse_raw is not None and len(szse_raw) == 0:
        return None, 'empty'
    return None, 'fail'


def update_scale(target_dates, conn, dry_run=False):
    """更新一个或多个日期的 ETF 规模数据到 etf.db

    逐日抓取，每成功一个日期写一个事务（部分失败不影响已写入日期）。
    写入后回查当日总规模，与抓取值核对到2位小数。
    """
    log("=" * 60)
    log(f"[规模更新] 目标日期: {', '.join(target_dates) if target_dates else '(无)'}")
    log("=" * 60)

    if not target_dates:
        log("  没有需要更新的日期")
        return True

    # 逐日抓取并写入
    updated_dates = []
    skipped_dates = []
    failed_dates = []
    for i, d in enumerate(target_dates):
        day_df, status = fetch_one_day_scale(d)
        if status == 'ok':
            n_sh = len(day_df[day_df['市场'] == '上海'])
            n_sz = len(day_df[day_df['市场'] == '深圳'])
            fetched_yi = day_df['基金规模(份)'].sum() / 100_000_000
            log(f"  [{d}] 获取 {len(day_df)} 条 (沪 {n_sh} / 深 {n_sz}), 总规模 {fetched_yi:.2f} 亿份")

            if dry_run:
                updated_dates.append(d)
                log(f"  [{d}] [DRY RUN] 跳过写入")
            else:
                n = db.replace_scale_for_date(conn, d, day_df)
                # 写入后回查核对（CLAUDE.md 铁律：数据必须可追溯核实）
                d_iso = db.norm_date(d)
                db_sum = conn.execute(
                    "SELECT SUM(shares) FROM fund_scale WHERE date = ?",
                    (d_iso,)).fetchone()[0] or 0
                db_yi = db_sum / 100_000_000
                if abs(db_yi - fetched_yi) > 0.005:
                    log(f"  [{d}] [FAIL] 写入后核对不符: DB {db_yi:.2f} vs 抓取 {fetched_yi:.2f} 亿份")
                    failed_dates.append(d)
                else:
                    updated_dates.append(d)
                    log(f"  [{d}] [OK] 写入 {n} 行, 回查核对一致 ({db_yi:.2f} 亿份)")
        elif status == 'empty':
            skipped_dates.append(d)
            log(f"  [{d}] 两所均无数据，跳过（节假日或数据未发布）")
        else:
            failed_dates.append(d)
            log(f"  [{d}] [FAIL] 数据不完整，本次不写入该日期")
        if i < len(target_dates) - 1:
            time.sleep(REQUEST_DELAY)

    if skipped_dates:
        log(f"  跳过 {len(skipped_dates)} 个无数据日期: {', '.join(skipped_dates)}")
    if failed_dates:
        log(f"  [WARN] 失败 {len(failed_dates)} 个日期: {', '.join(failed_dates)}，下次运行将自动重试")

    if not updated_dates:
        if failed_dates:
            log("[FAIL] 没有写入任何新数据，规模更新中止")
            return False
        log("  所有目标日期均无数据（节假日或未发布），无需更新")
        return True

    log(f"  本次更新 {len(updated_dates)} 个日期" + ("（预览模式，未写入）" if dry_run else ""))
    if not dry_run:
        n_total = conn.execute("SELECT COUNT(*) FROM fund_scale").fetchone()[0]
        lo, hi = conn.execute("SELECT MIN(date), MAX(date) FROM fund_scale").fetchone()
        log(f"  fund_scale 现有 {n_total} 行, 日期范围 {lo} ~ {hi}")
    return len(failed_dates) == 0


# ===================================================================
#  价格数据更新
# ===================================================================

def update_prices(target_date, conn, dry_run=False, start_date=None):
    """更新 ETF 价格数据到 etf.db (fund_price)

    策略：
    1. 从 funds 表获取所有基金代码
    2. 对每只基金，用 ak.fund_etf_hist_em() 抓取日期范围内的价格
    3. 按 (date, code) 覆盖写入 fund_price

    start_date: 抓取起点（YYYYMMDD，可选）。回填模式下传入最早缺失日期，
                默认从 target_date 往前5天（覆盖周末/节假日）。
    """
    log("=" * 60)
    log(f"[价格更新] 目标日期: {target_date}")
    log("=" * 60)

    # 1. 获取基金代码列表
    log(">>> 读取基金代码列表...")
    codes = db.fund_codes(conn)
    if not codes:
        log("  [FAIL] funds 表为空，无法获取基金代码")
        return False
    log(f"  共 {len(codes)} 只基金")

    # 2. 计算日期范围（往前5天覆盖周末/节假日）
    target_dt = pd.to_datetime(target_date)
    base_dt = pd.to_datetime(start_date) if start_date else target_dt
    start_dt = base_dt - timedelta(days=5)
    start_str = start_dt.strftime('%Y%m%d')
    end_str = target_dt.strftime('%Y%m%d')
    log(f"  抓取日期范围: {start_str} ~ {end_str}")

    # 3. 逐只抓取（只抓还没有目标日期数据的基金）
    target_iso = target_dt.strftime('%Y-%m-%d')
    max_by_code = db.price_max_date_by_code(conn)
    codes_to_fetch = [c for c in codes if max_by_code.get(c, '') < target_iso]
    codes_to_skip = len(codes) - len(codes_to_fetch)

    log(f"  需抓取: {len(codes_to_fetch)} 只, 已是最新: {codes_to_skip} 只")

    if not codes_to_fetch:
        log("  所有基金价格已是最新，无需更新")
        return True

    all_new = []
    success_count = 0
    fail_count = 0
    total = len(codes_to_fetch)

    log(f">>> 开始抓取价格数据 (共 {total} 只)...")
    for i, code in enumerate(codes_to_fetch):
        try:
            df = ak.fund_etf_hist_em(
                symbol=code,
                period='daily',
                start_date=start_str,
                end_date=end_str,
                adjust='qfq'
            )

            if df is not None and len(df) > 0:
                df = df[['日期', '收盘']].copy()
                df.columns = ['日期', '收盘价']
                df.insert(0, '基金代码', code)
                all_new.append(df)
                success_count += 1

            if (i + 1) % 200 == 0:
                log(f"  进度: {i+1}/{total}, 成功 {success_count}")

        except Exception as e:
            fail_count += 1
            if fail_count <= 5:
                log(f"  [WARN] {code} 抓取失败: {e}")

        # 控制请求频率
        if (i + 1) % 15 == 0:
            time.sleep(0.3)

    log(f"  价格抓取完成: 成功 {success_count}, 失败 {fail_count}")

    if not all_new:
        log("  [WARN] 没有新价格数据")
        return True

    # 4. 合并写入
    new_prices = pd.concat(all_new, ignore_index=True)
    log(f"  新获取价格数据: {len(new_prices)} 行")

    if dry_run:
        log("[DRY RUN] 以上为预览，未写入")
        log(f"  将写入 {len(new_prices)} 行到 fund_price")
        return True

    # 5. 写入（INSERT OR REPLACE，按 (date, code) 覆盖）
    log(">>> 写入 fund_price ...")
    n = db.upsert_prices(conn, new_prices)
    n_total = conn.execute("SELECT COUNT(*) FROM fund_price").fetchone()[0]
    log(f"  [OK] 写入 {n} 行, fund_price 现有 {n_total} 行, 最新日期 {db.max_price_date(conn)}")
    return True


# ===================================================================
#  主入口
# ===================================================================

def main():
    parser = argparse.ArgumentParser(
        description='ETF 每日数据更新 (规模 + 价格)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python daily_update.py                       # 自动补齐所有缺失日期（含昨日）
  python daily_update.py --date 20260629       # 仅更新指定日期
  python daily_update.py --scale-only          # 仅更新规模
  python daily_update.py --price-only          # 仅更新价格
  python daily_update.py --dry-run             # 预览模式
        """
    )
    parser.add_argument('--date', type=str, default='',
                        help='目标日期 YYYYMMDD (默认: 自动检测并补齐所有缺失日期)')
    parser.add_argument('--scale-only', action='store_true',
                        help='仅更新规模数据')
    parser.add_argument('--price-only', action='store_true',
                        help='仅更新价格数据')
    parser.add_argument('--dry-run', action='store_true',
                        help='预览模式，不写入文件')
    args = parser.parse_args()

    target_date = get_target_date(args.date)

    log("=" * 60)
    log(f"ETF 每日数据更新")
    if args.date:
        log(f"  目标日期: {args.date}")
    else:
        log(f"  目标日期: 自动检测缺失日期 (截至 {target_date})")
    log(f"  模式: {'DRY RUN (预览)' if args.dry_run else '正式更新'}")
    log("=" * 60)

    start_time = time.time()
    scale_ok = True
    price_ok = True
    scale_dates = []

    conn = db.connect()

    # 规模更新
    if not args.price_only:
        try:
            existing_dates = db.scale_dates(conn)
            if existing_dates:
                log(f"  数据库现有 {len(existing_dates)} 个日期: "
                    f"{existing_dates[0]} ~ {existing_dates[-1]}")
            else:
                log("  数据库为空（首次运行）")

            if args.date:
                scale_dates = [args.date]
            else:
                scale_dates = detect_missing_dates(existing_dates, target_date)
                if scale_dates:
                    log(f"  自动检测到 {len(scale_dates)} 个待更新日期: {', '.join(scale_dates)}")
                else:
                    log("  规模数据无缺失日期，已是最新")

            scale_ok = update_scale(scale_dates, conn, dry_run=args.dry_run)
        except Exception as e:
            log(f"[FAIL] 规模更新异常: {e}")
            log(traceback.format_exc())
            scale_ok = False

    # 价格更新
    if not args.scale_only:
        try:
            price_start = min(scale_dates) if scale_dates else None
            price_ok = update_prices(target_date, conn, dry_run=args.dry_run,
                                     start_date=price_start)
        except Exception as e:
            log(f"[FAIL] 价格更新异常: {e}")
            log(traceback.format_exc())
            price_ok = False

    conn.close()

    elapsed = time.time() - start_time
    log("=" * 60)
    log(f"更新完成! 耗时 {elapsed:.0f} 秒 ({elapsed/60:.1f} 分钟)")
    log(f"  规模: {'[OK] 成功' if scale_ok else '[FAIL] 失败'}")
    log(f"  价格: {'[OK] 成功' if price_ok else '[FAIL] 失败'}")

    if scale_ok and price_ok:
        log("  全部更新成功!")
        return 0
    elif scale_ok or price_ok:
        log("  部分更新成功")
        return 1
    else:
        log("  更新失败")
        return 2


if __name__ == '__main__':
    sys.exit(main())
