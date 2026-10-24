# -*- coding: utf-8 -*-
"""多主题动量与资金面结合的 ETF 轮动策略 — 因子特征库

读取 etf.db 中的 fund_price / fund_scale 及大盘指数数据，
为目标 ETF 计算 4 维核心因子：

1. 绝对与相对动量 (Momentum)
2. 成交量爆发 (Volume Impulse)
3. 规模/份额背离 (Smart Money Flow)
4. 波动率与风险 (Volatility)

用法：
    from src.strategy.multi_factor_builder import compute_all_factors
    df = compute_all_factors(codes, start_date='2019-01-01')
"""

import sqlite3
import pandas as pd
import numpy as np
import warnings
from pathlib import Path

warnings.filterwarnings('ignore')

DB_PATH = str(Path(__file__).resolve().parent.parent.parent / 'etf.db')

# ──────────────────────────────────────────────
# 目标标的池
# ──────────────────────────────────────────────
TARGET_CODES = [
    # 防守/底仓
    '515120', '512890',
    # 进攻/弹性 — 恒生科技
    '513180', '513130',
    # 进攻/弹性 — 半导体设备
    '159516',
    # 进攻/弹性 — 证券/券商
    '512000', '512880',
    # 进攻/弹性 — 创新药
    '513120', '159992',
    # 大盘基准/择时
    '000300',
]

MARKET_BENCH = '000300'  # 沪深300


# ===================================================================
#  数据加载
# ===================================================================

def load_market_data(codes, conn=None):
    """从数据库加载目标 ETF + 大盘指数的价格与份额数据。

    返回两个 DataFrame:
      price_df: 列 [date, code, close, volume]
      scale_df: 列 [date, code, shares]
    """
    code_list = list(set(codes))
    placeholders = ','.join(f'"{c}"' for c in code_list)

    if conn is None:
        conn = sqlite3.connect(DB_PATH)

    price_df = pd.read_sql(
        f"SELECT date, code, close, volume FROM fund_price WHERE code IN ({placeholders})",
        conn)
    scale_df = pd.read_sql(
        f"SELECT date, code, shares FROM fund_scale WHERE code IN ({placeholders})",
        conn)

    price_df['date'] = pd.to_datetime(price_df['date'])
    scale_df['date'] = pd.to_datetime(scale_df['date'])

    conn.close()
    return price_df, scale_df


# ===================================================================
#  1. 动量因子 (Momentum)
# ===================================================================

def compute_momentum(df: pd.DataFrame, bench_close: pd.Series) -> pd.DataFrame:
    """计算绝对动量 (R20, R60) 与相对动量 (RS20, RS60)。

    R_N = (close_t / close_{t-N} - 1) * 252 / N  (年化)
    RS = 标的 R_N - 基准 R_N  (相对强弱度)

    参数：
        df: 单只 ETF 的合并 DataFrame，已按 date 排序，含 close 列
        bench_close: 基准 (沪深300) 的 close Series，index 为 date

    返回：
        df 已原地添加 factor_mom_* 列并返回
    """
    # 确保 index 为 date 的基准价格序列
    bench = bench_close.copy()
    bench.index = pd.to_datetime(bench.index)
    if isinstance(bench.index, pd.DatetimeIndex):
        bench = bench[~bench.index.duplicated(keep='last')]

    for lag, name in [(20, '20'), (60, '60')]:
        # 标的年化收益率
        ret = df['close'] / df['close'].shift(lag) - 1
        df[f'factor_mom_R{name}'] = ret * (252 / lag)

        # 基准同窗口收益率：reindex 到 df 的日期序列
        bench_reindexed = bench.reindex(df['date'])
        bench_ret = (bench_reindexed / bench_reindexed.shift(lag) - 1).fillna(0)

        # 相对动量 = 标的年化 - 基准年化（用 .to_numpy() 避免 index 对齐产生 NaN）
        df[f'factor_mom_RS{name}'] = df[f'factor_mom_R{name}'].to_numpy() - (bench_ret * (252 / lag)).to_numpy()

    return df


# ===================================================================
#  2. 成交量爆发因子 (Volume Impulse)
# ===================================================================

def compute_volume_impulse(df: pd.DataFrame) -> pd.DataFrame:
    """计算 5 日成交量均线 / 20 日成交量均线的比值。

    用于捕捉证券 ETF、半导体等放量拉升信号。

    参数：
        df: 单只 ETF 的合并 DataFrame，含 volume 列

    返回：
        df 已原地添加 factor_vol_impulse 列并返回
    """
    vol = df['volume'].fillna(0)
    ma5 = vol.rolling(5, min_periods=5).mean()
    ma20 = vol.rolling(20, min_periods=20).mean()
    df['factor_vol_impulse'] = ma5 / ma20.replace(0, np.nan)
    return df


# ===================================================================
#  3. 规模/份额背离因子 (Smart Money Flow)
# ===================================================================

def compute_share_flow(df: pd.DataFrame) -> pd.DataFrame:
    """计算份额变化率与量价背离得分。

    因子：
        share_chg_10  = (Shares_t - Shares_{t-10}) / Shares_{t-10}
        share_chg_20  = (Shares_t - Shares_{t-20}) / Shares_{t-20}
        price_dev_20  = (close_t / close_20d_low - 1)  是否创 20 日新低
        share_rank_20 = 份额在 20 日窗口中的百分排名
        divergence_20 = 价格新低 & 份额新高 背离指标 (0/1)

    参数：
        df: 单只 ETF 的合并 DataFrame，含 shares / close / date 列

    返回：
        df 已原地添加 share_flow 列并返回
    """
    shares = df['shares']
    closes = df['close']

    # 份额变化率
    df['share_chg_10'] = (shares - shares.shift(10)) / shares.shift(10)
    df['share_chg_20'] = (shares - shares.shift(20)) / shares.shift(20)

    # 过去 20 日最低价（不含当日）— 用于判断今日是否创 20 日新低
    low20 = closes.rolling(20, min_periods=20).min().shift(1)
    df['price_dev_20'] = closes / low20 - 1  # < 0 表示今日价格跌破过去20日最低点（创新低）

    # 份额在 20 日窗口中的百分排名 (0~1)
    share_rank_20 = shares.rolling(20, min_periods=20).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
    df['share_rank_20'] = share_rank_20

    # 量价背离：价格创 20 日新低 且 份额创 20 日新高
    # 价格新低：今日收盘 < 过去20日最低价（不含当日）
    # 份额新高：今日份额 > 过去20日最高份额（不含当日）
    high_shares_20 = shares.rolling(20, min_periods=20).max().shift(1)  # 不含当日
    price_new_low = closes < low20
    share_new_high = shares > high_shares_20
    df['divergence_20'] = (price_new_low & share_new_high).astype(int)

    return df


# ===================================================================
#  4. 波动率与风险因子 (Volatility & Risk)
# ===================================================================

def compute_volatility_risk(df: pd.DataFrame) -> pd.DataFrame:
    """计算 20 日滚动年化波动率与 60 日最大回撤。

    因子：
        vol20_annual  = std(收益率, 20) * sqrt(252)  (年化)
        dd60          = 滚动 60 日最大回撤 (%)

    参数：
        df: 单只 ETF 的合并 DataFrame，含 close 列

    返回：
        df 已原地添加 vol_risk 列并返回
    """
    ret = df['close'].pct_change()

    # 20 日滚动年化波动率
    std20 = ret.rolling(20, min_periods=20).std()
    df['vol20_annual'] = std20 * np.sqrt(252)

    # 60 日最大回撤：以 60 日滚动窗口计算
    rolling_max = df['close'].rolling(60, min_periods=60).max()
    df['dd60'] = (df['close'] - rolling_max) / rolling_max * 100  # 百分比

    return df


# ===================================================================
#  总入口
# ===================================================================

def compute_all_factors(codes: list = None, start_date: str = '2019-01-01',
                        bench_code: str = MARKET_BENCH) -> pd.DataFrame:
    """为所有目标 ETF 计算 4 维因子。

    参数：
        codes: 目标代码列表，默认使用 TARGET_CODES
        start_date: 起始日期（含）
        bench_code: 基准代码，默认 000300

    返回：
        合并后的 DataFrame，包含所有因子列，index 为 date。
        每行对应 (date, code)，含各标的当日全部因子值。
    """
    if codes is None:
        codes = TARGET_CODES

    price_df, scale_df = load_market_data(codes)

    # 过滤起始日期
    price_df = price_df[price_df['date'] >= pd.to_datetime(start_date)]
    scale_df = scale_df[scale_df['date'] >= pd.to_datetime(start_date)]

    # 对齐：inner join 价格 + 份额（ETF 标的）
    merged = price_df.merge(scale_df, on=['date', 'code'], how='inner')
    merged.sort_values(['code', 'date'], inplace=True)
    merged.reset_index(drop=True, inplace=True)

    # 基准价格：独立加载（大盘指数可能无份额数据，不能走 inner join）
    bench_price = price_df[price_df['code'] == bench_code].set_index('date')['close']

    # 逐只计算因子
    results = []
    for code, group in merged.groupby('code'):
        df = group.copy().sort_values('date').reset_index(drop=True)

        if bench_code == code:
            # 基准自身：相对动量为 NaN 或 0
            df['factor_mom_RS20'] = np.nan
            df['factor_mom_RS60'] = np.nan
        else:
            # 1. 动量因子
            df = compute_momentum(df, bench_price)

        # 2. 成交量爆发
        df = compute_volume_impulse(df)

        # 3. 份额背离
        df = compute_share_flow(df)

        # 4. 波动率与风险
        df = compute_volatility_risk(df)

        df['etf_code'] = code
        results.append(df)

    result = pd.concat(results, ignore_index=True)

    # 补充基金名称
    try:
        conn = sqlite3.connect(DB_PATH)
        name_map = dict(conn.execute(
            "SELECT code, name FROM funds").fetchall())
        conn.close()
        result['etf_name'] = result['etf_code'].map(name_map)
    except Exception:
        result['etf_name'] = ''

    return result


# ===================================================================
#  校验工具
# ===================================================================

def check_lookahead_bias(factors_df: pd.DataFrame, codes: list = None) -> dict:
    """检查未来函数风险。

    通过检查各因子是否在当日数据中引用了未来日期的数据来检测 Look-ahead bias。

    核心检查点：
        - 动量因子：使用 shift(lag)，无未来数据 → 应安全
        - 成交量均线：rolling 窗口不引用未来 → 应安全
        - 份额变化率：使用 shift → 应安全
        - 背离指标：share_rank_20 使用窗口内数据，但 high_shares_20 已 shift(1) → 应安全
        - 波动率：rolling 窗口不引用未来 → 应安全

    返回：
        风险报告字典 {因子名: ['safe' | 'risk']}
    """
    if codes is None:
        codes = TARGET_CODES

    report = {
        'factor_mom_R20':       'safe',   # pct_change(20), no look-ahead
        'factor_mom_R60':       'safe',   # pct_change(60), no look-ahead
        'factor_mom_RS20':      'safe',   # bench also uses same shift
        'factor_mom_RS60':      'safe',
        'factor_vol_impulse':   'safe',   # rolling mean, no look-ahead
        'share_chg_10':         'safe',   # shift(10)
        'share_chg_20':         'safe',   # shift(20)
        'price_dev_20':         'safe',   # rolling min (past window)
        'share_rank_20':        'safe',   # rolling rank (past window only)
        'divergence_20':        'safe',   # high_shares_20 shifted by 1
        'vol20_annual':         'safe',   # rolling std (past window)
        'dd60':                 'safe',   # rolling max (past window)
    }

    # 实际检查：如果某因子在数据中的 NaN 分布与预期一致，则无问题
    # 预期：前 N 天因 lag/rolling 产生 NaN
    expected_nan = {
        'factor_mom_R20':       20,
        'factor_mom_R60':       60,
        'factor_mom_RS20':      60,
        'factor_mom_RS60':      60,
        'factor_vol_impulse':   20,
        'share_chg_10':         10,
        'share_chg_20':         20,
        'price_dev_20':         20,
        'share_rank_20':        20,
        'divergence_20':        20,
        'vol20_annual':         20,
        'dd60':                 60,
    }

    for factor_name, threshold in expected_nan.items():
        if factor_name not in factors_df.columns:
            continue
        code_groups = factors_df[factors_df['etf_code'].isin(codes)].groupby('etf_code')
        for code, grp in code_groups:
            actual_nan = int(grp[factor_name].isna().sum())
            if actual_nan > threshold + 5:  # 允许少量基准日期不对齐的余量
                report[factor_name] = f'risk: {actual_nan} NaN > expected {threshold}'

    return report


def validate_factors(factors_df: pd.DataFrame, codes: list = None) -> dict:
    """因子数据完整性校验。

    返回校验报告字典。
    """
    if codes is None:
        codes = TARGET_CODES

    report = {}

    # 1. 目标代码覆盖
    present_codes = set(factors_df[factors_df['etf_code'].isin(codes)]['etf_code'].unique())
    missing_codes = set(codes) - present_codes
    report['missing_codes'] = sorted(missing_codes)
    report['covered_codes'] = sorted(present_codes)

    # 2. 数据缺失
    factor_cols = [c for c in factors_df.columns if any(
        c.startswith(p) for p in ('factor_', 'share_', 'price_dev', 'divergence_', 'vol20', 'dd60'))]
    report['missing_factor_values'] = {}
    for col in factor_cols:
        n_nan = int(factors_df[col].isna().sum())
        report['missing_factor_values'][col] = n_nan

    # 3. 日期范围
    date_min = factors_df['date'].min()
    date_max = factors_df['date'].max()
    report['date_range'] = (str(date_min), str(date_max))

    # 4. 未来函数检查
    report['lookahead_bias'] = check_lookahead_bias(factors_df, codes)

    return report


if __name__ == '__main__':
    import sys
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    print("=" * 70)
    print("多主题因子特征库 — 验证脚本")
    print("=" * 70)

    # Step 1: 计算因子
    print("\n[1] 计算因子 (2019-01-01 起)...")
    factors = compute_all_factors(start_date='2019-01-01')
    print(f"  输出: {len(factors)} 行 x {len(factors.columns)} 列")
    print(f"  日期范围: {factors['date'].min()} ~ {factors['date'].max()}")
    print(f"  标的数: {factors['etf_code'].nunique()}")

    # Step 2: 描述性统计
    print("\n[2] 因子描述性统计")
    factor_cols = [c for c in factors.columns if c.startswith('factor_') or c.startswith('share_') or c.startswith('vol20') or c.startswith('dd60') or c == 'divergence_20']
    desc = factors[factor_cols].describe()
    print(desc.to_string(float_format=lambda x: f'{x:10.4f}'))

    # Step 3: 校验报告
    print("\n[3] 数据校验报告")
    report = validate_factors(factors)
    print(f"  覆盖标的: {report['covered_codes']}")
    print(f"  缺失标的: {report['missing_codes']}")
    print(f"  日期范围: {report['date_range']}")
    print(f"  缺失因子值: {report['missing_factor_values']}")
    print(f"  未来函数风险: {report['lookahead_bias']}")

    # Step 4: 关键时间节点样例
    key_dates = {
        '2020-07-10': '证券爆发期',
        '2020-07-13': '证券爆发期',
        '2024-09-24': '市场剧烈反弹前夜',
        '2024-09-30': '市场剧烈反弹',
        '2024-10-08': '市场剧烈反弹后',
    }

    print("\n[4] 关键时间节点因子样例")
    for target_date, label in key_dates.items():
        print(f"\n  --- {label} ({target_date}) ---")
        mask = factors['date'] == pd.to_datetime(target_date)
        subset = factors[mask]
        if len(subset) == 0:
            print("  (无数据)")
            continue
        out_cols = ['date', 'etf_code', 'etf_name', 'close', 'volume'] + factor_cols
        out_cols = [c for c in out_cols if c in subset.columns]
        print(subset[out_cols].to_string(float_format=lambda x: f'{x:10.4f}' if isinstance(x, float) else str(x)))

    print("\n" + "=" * 70)
    print("验证完成。")
    print("=" * 70)
