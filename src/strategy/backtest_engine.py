# -*- coding: utf-8 -*-
"""多主题动量与资金面 ETF 轮动策略 — Step 3: 回测引擎

读取 Step 2 的 generate_signals() 输出，用真实价格做 T+1 调仓净值回放，
输出完整绩效评估、风险归因、对比分析。

用法:
    from src.strategy.backtest_engine import run_backtest, plot_backtest
    run_backtest(start_date='2019-04-01')
    plot_backtest(out_dir='output')
"""

import sys
import io
import warnings
import os
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# -- 确保脚本模式下也能 import 同包模块 --
ROOT = str(Path(__file__).resolve().parent.parent.parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.strategy.signal_generator import generate_signals
from src.strategy.multi_factor_builder import DB_PATH
import sqlite3


# ===================================================================
#  回测参数
# ===================================================================

DEFAULT_PARAMS = {
    'lookback': 60,
    'max_turnover': 0.5,
    'min_hold_days': 1,
    'enable_truncate': True,
    'enable_cost': True,
    'warmup': 120,
}


# ===================================================================
#  T+1 调仓净值回放
# ===================================================================

def run_backtest(
    start_date: str = '2019-04-01',
    params: dict = None,
) -> dict:
    """用真实价格回放 Step 2 的每日持仓权重。

    调仓逻辑（T+1 执行）：
        day D 收盘生成信号 -> 确定目标权重 w_target
        day D+1 调仓执行 -> 用 D+1 日收益累积 w_target 权重的组合收益

    返回:
        {nav, daily_rets, trades, params,
         benchmark_nav, cash_nav, riskoff_nav}
    """
    params = {**DEFAULT_PARAMS, **(params or {})}

    # 1) 信号 + 快速查找表 {date -> 当日持仓 (code, weight)}
    sig = generate_signals(start_date=start_date)
    all_dates = sorted(sig['date'].unique())
    sig_dict = {}
    for d, day in sig.groupby('date'):
        entries = []
        for _, row in day.iterrows():
            if row['weight'] > 0.01:
                entries.append((row['etf_code'], row['weight']))
        if entries:
            sig_dict[d] = entries

    # 2) 价格
    conn = sqlite3.connect(DB_PATH)
    price_all = pd.read_sql("SELECT date, code, close FROM fund_price", conn)
    price_all['date'] = pd.to_datetime(price_all['date'])
    conn.close()

    # 构建价格面板 {date -> {code -> close}}
    price_map = {}
    for d, day in price_all.groupby('date'):
        price_map[d] = dict(zip(day['code'], day['close']))

    # 3) 基准: 沪深300
    conn = sqlite3.connect(DB_PATH)
    bench_close = pd.read_sql("SELECT date, close FROM fund_price WHERE code='000300'", conn)
    bench_close['date'] = pd.to_datetime(bench_close['date'])
    conn.close()
    bench_map = dict(zip(bench_close['date'], bench_close['close']))

    # 4) 初始化
    nav = pd.Series(np.nan, index=all_dates)
    daily_rets = pd.Series(np.nan, index=all_dates)
    bench_nav = pd.Series(np.nan, index=all_dates)
    cash_nav = pd.Series(np.nan, index=all_dates)
    riskoff_nav = pd.Series(np.nan, index=all_dates)

    trades = []
    current_hold = {}        # {code: weight}
    hold_days = {}           # {code: days_in_position}
    nav.iloc[0] = 1.0
    bench_nav.iloc[0] = 1.0
    cash_nav.iloc[0] = 1.0
    riskoff_nav.iloc[0] = 1.0

    for i, date in enumerate(all_dates):
        # 基准净值
        if i == 0:
            bench_nav.iloc[0] = 1.0
            cash_nav.iloc[0] = 1.0
            riskoff_nav.iloc[0] = 1.0
        else:
            prev_bench = bench_map.get(all_dates[i - 1], 1.0)
            curr_bench = bench_map.get(date, prev_bench)
            if prev_bench > 0 and curr_bench > 0:
                bench_nav.iloc[i] = bench_nav.iloc[i - 1] * (curr_bench / prev_bench)
                cash_nav.iloc[i] = cash_nav.iloc[i - 1]  # 无风险
                # riskoff: 仅 512890
                r_rb = _get_return(price_map, '512890', date, all_dates, i - 1)
                riskoff_nav.iloc[i] = riskoff_nav.iloc[i - 1] * (1 + r_rb)

        # 调仓: 取前一日信号，在 D 日执行
        # 预热期: 前 warmup 个信号日只做净值同步，不执行调仓
        if i <= params['warmup']:
            nav.iloc[i] = nav.iloc[i - 1] if i > 0 else 1.0
            daily_rets.iloc[i] = 0
            continue

        prev_date = all_dates[i - 1]
        entries = sig_dict.get(prev_date, [])

        if not entries:
            # 无信号 -> 空仓
            turnover = sum(current_hold.values())
            if turnover > 0:
                trades.append({
                    'date': date,
                    'turnover': turnover,
                    'nav': nav.iloc[i - 1],
                    'target_codes': [],
                    'target_weights': {},
                })
            current_hold = {}
            hold_days = {}
            nav.iloc[i] = nav.iloc[i - 1]
            daily_rets.iloc[i] = 0
            continue

        # 5) 执行调仓（遵守 min_hold_days 约束：新买后至少持仓 N 天才能卖出）
        target_weights = dict(entries)
        min_hold = params['min_hold_days']

        # 换手
        turnover = 0.0
        sell_codes = set(current_hold.keys()) - set(target_weights.keys())
        for code in sell_codes:
            # 持仓天数不足 → 强制留在目标中
            if min_hold > 0 and hold_days.get(code, 0) < min_hold:
                target_weights[code] = current_hold[code]
            else:
                turnover += current_hold[code]
                hold_days.pop(code, None)
        for code in set(target_weights.keys()) - set(current_hold.keys()):
            turnover += target_weights[code]
        for code in set(current_hold.keys()) & set(target_weights.keys()):
            delta = target_weights[code] - current_hold.get(code, 0)
            if delta < 0:
                # 减仓：持仓天数不足时不允许减仓（全进或全不动）
                if min_hold > 0 and hold_days.get(code, 0) < min_hold:
                    target_weights[code] = current_hold[code]
                    delta = 0
                turnover += abs(delta)

        # 换手上限截断：同比例缩减调仓幅度，未成交部分留在现金
        if params['enable_truncate'] and turnover > params['max_turnover']:
            scale = params['max_turnover'] / (turnover + 1e-12)
            # 记录被截断的调仓额
            cut = turnover - params['max_turnover']
            # 对所有调仓方向同比例缩减
            for code in sell_codes:
                if code in current_hold and code not in target_weights:
                    # 卖出被截断 → 改为部分卖出
                    orig_sell = current_hold[code]
                    new_sell = orig_sell * scale
                    target_weights[code] = current_hold[code] - new_sell
                elif code in current_hold and code in target_weights:
                    # 减仓被截断
                    delta_orig = current_hold[code] - target_weights.get(code, 0)
                    if delta_orig > 0:
                        delta_new = delta_orig * scale
                        target_weights[code] = current_hold[code] - delta_new
            for code in set(target_weights.keys()) - set(current_hold.keys()):
                # 新增仓位被截断 → 只买一部分
                target_weights[code] *= scale
            for code in set(current_hold.keys()) & set(target_weights.keys()):
                delta = target_weights[code] - current_hold.get(code, 0)
                if delta > 0:
                    target_weights[code] = current_hold[code] + delta * scale
            turnover = params['max_turnover']

        # 更新持仓
        current_hold = {c: w for c, w in target_weights.items() if w > 0.01}

        # 记录交易
        if turnover > 0:
            trades.append({
                'date': date,
                'turnover': round(turnover, 4),
                'nav': nav.iloc[i - 1],
                'target_codes': list(target_weights.keys()),
                'target_weights': {k: round(v, 3) for k, v in target_weights.items()},
            })

        for code in list(hold_days.keys()):
            if code not in target_weights:
                hold_days.pop(code, None)
        for code in target_weights:
            hold_days[code] = hold_days.get(code, 0) + 1

        # 组合收益
        port_ret = 0.0
        for code, w in current_hold.items():
            r = _get_return(price_map, code, date, all_dates, i - 1)
            port_ret += w * r

        nav.iloc[i] = nav.iloc[i - 1] * (1 + port_ret)
        daily_rets.iloc[i] = port_ret

    # 6) 交易成本
    total_cost = 0.0
    if params['enable_cost'] and trades:
        total_turnover = sum(t['turnover'] for t in trades)
        cost_rate = 0.001  # ETF 佣金约千分之一
        total_cost = total_turnover * cost_rate

    return {
        'nav': nav,
        'daily_rets': daily_rets,
        'trades': trades,
        'params': params,
        'benchmark_nav': bench_nav,
        'cash_nav': cash_nav,
        'riskoff_nav': riskoff_nav,
        'total_cost': total_cost,
    }


def _get_return(price_map, code, date, all_dates, prev_i):
    """获取某标的在某日相对于前一日的收益率。"""
    try:
        if not code or pd.isna(code):
            return 0.0
        prev_price = price_map.get(all_dates[prev_i], {}).get(code)
        curr_price = price_map.get(date, {}).get(code)
        if prev_price and curr_price and prev_price > 0:
            return curr_price / prev_price - 1
    except (KeyError, TypeError, IndexError):
        pass
    return 0.0


# ===================================================================
#  绩效评估
# ===================================================================

def evaluate_performance(result: dict) -> dict:
    """计算完整绩效指标。"""
    nav = result['nav']
    daily_rets = result['daily_rets']
    bench = result['benchmark_nav']
    cash = result['cash_nav']
    riskoff = result['riskoff_nav']

    valid_nav = nav.dropna()
    valid_rets = daily_rets.dropna()

    if len(valid_nav) < 10:
        return {'note': 'insufficient data'}

    total_days = (valid_nav.index[-1] - valid_nav.index[0]).days
    years = total_days / 252.0 if total_days > 0 else 1

    cagr = valid_nav.iloc[-1] ** (1.0 / years) - 1.0 if years > 0 else 0
    bench_cagr = bench.iloc[-1] ** (1.0 / years) - 1.0 if years > 0 else 0
    cash_cagr = cash.iloc[-1] ** (1.0 / years) - 1.0 if years > 0 else 0
    riskoff_cagr = riskoff.iloc[-1] ** (1.0 / years) - 1.0 if years > 0 else 0

    # 最大回撤
    rolling_max = valid_nav.cummax()
    drawdown = (valid_nav - rolling_max) / (rolling_max + 1e-12)
    max_dd = drawdown.min()

    # 夏普
    if len(valid_rets) > 2 and valid_rets.std() > 0:
        sharpe = (valid_rets.mean() - 0.02 / 252) / valid_rets.std() * np.sqrt(252)
    else:
        sharpe = 0

    bench_rets = bench.pct_change().dropna()
    if len(bench_rets) > 2 and bench_rets.std() > 0:
        bench_sharpe = (bench_rets.mean() - 0.02 / 252) / bench_rets.std() * np.sqrt(252)
    else:
        bench_sharpe = 0

    # 信息比率
    excess_rets = valid_rets - bench_rets.reindex(valid_rets.index).fillna(0)
    if len(excess_rets) > 2 and excess_rets.std() > 0:
        ir = excess_rets.mean() / excess_rets.std() * np.sqrt(252)
    else:
        ir = 0

    # 卡尔玛
    calmar = cagr / abs(max_dd) if abs(max_dd) > 0 else 0

    # 日胜率
    win_rate = np.mean(valid_rets > 0)

    # 跑赢基准天数
    match_idx = valid_rets.index.intersection(bench_rets.index)
    out_perf_days = 0
    if len(match_idx) > 10:
        out_perf_days = np.mean(valid_rets.reindex(match_idx) > bench_rets.reindex(match_idx))

    # 各标的交易统计
    code_stats = {}
    for t in result['trades']:
        for code, w in t.get('target_weights', {}).items():
            code_stats.setdefault(code, {'n': 0, 'tw': 0, 'codes': set()})
            code_stats[code]['n'] += 1
            code_stats[code]['tw'] += w
            code_stats[code]['codes'].add(code)

    return {
        'strategy_total_ret': round(valid_nav.iloc[-1] - 1, 4),
        'strategy_cagr': round(cagr, 4),
        'bench_cagr': round(bench_cagr, 4),
        'cash_cagr': round(cash_cagr, 4),
        'riskoff_cagr': round(riskoff_cagr, 4),
        'max_dd': round(max_dd, 4),
        'sharpe': round(sharpe, 3),
        'bench_sharpe': round(bench_sharpe, 3),
        'information_ratio': round(ir, 3),
        'calmar': round(calmar, 2),
        'win_rate': round(win_rate, 4),
        'out_perf_days': round(out_perf_days, 4),
        'n_days': len(valid_rets),
        'n_trades': len(result['trades']),
        'total_turnover': round(sum(t.get('turnover', 0) for t in result['trades']), 2),
        'total_cost': round(result.get('total_cost', 0), 4),
        'excess_vs_bench': round(valid_nav.iloc[-1] - bench.iloc[-1], 4),
        'code_stats': {
            k: {kk: vv for kk, vv in v.items() if kk != 'codes'}
            | {'code_names': sorted(v['codes'])}
            for k, v in code_stats.items()
        },
    }


# ===================================================================
#  绩效报告
# ===================================================================

def print_report(result: dict):
    """打印回测报告。"""
    perf = evaluate_performance(result)

    print("=" * 78)
    print("多主题轮动策略 -- Step 3 回测引擎")
    print("=" * 78)
    print(f"  回测区间: {result['nav'].dropna().index[0].date()} ~ {result['nav'].dropna().index[-1].date()}")
    print(f"  参数: {result['params']}")
    print(f"  调仓次数: {len(result['trades'])}")

    print(f"\n--- 绩效对比 ---")
    hdr = f"{'指标':<18s} {'策略':>12s} {'沪深300':>12s} {'现金':>12s} {'风险off':>12s}"
    print(hdr)
    print("-" * 78)

    rows = [
        ('累计收益', f"{perf['strategy_total_ret']:+.2%}", "---", "---", "---"),
        ('年化收益', f"{perf['strategy_cagr']:+.2%}", f"{perf['bench_cagr']:+.2%}",
         f"{perf['cash_cagr']:+.2%}", f"{perf['riskoff_cagr']:+.2%}"),
        ('最大回撤', f"{perf['max_dd']:+.2%}", "---", "---", "---"),
        ('夏普', f"{perf['sharpe']:.1f}", f"{perf['bench_sharpe']:.1f}", "---", "---"),
        ('信息比率', f"{perf['information_ratio']:.1f}", "---", "---", "---"),
        ('卡尔玛', f"{perf['calmar']:.1f}", "---", "---", "---"),
        ('日胜率', f"{perf['win_rate']:.2%}", "---", "---", "---"),
        ('日度跑赢基准', f"{perf['out_perf_days']:.2%}", "---", "---", "---"),
        ('净值超额', f"{perf['excess_vs_bench']:+.2%}", "---", "---", "---"),
        ('换手总计', f"{perf['total_turnover']:.1%}", "---", "---", "---"),
        ('交易成本', f"{perf['total_cost']:.2%}", "---", "---", "---"),
    ]
    for row in rows:
        print(f"{row[0]:<18s} {row[1]:>12s} {row[2]:>12s} {row[3]:>12s} {row[4]:>12s}")

    print(f"\n--- 各标的交易统计 ---")
    for code, stats in sorted(perf['code_stats'].items()):
        print(f"  {code}: 调仓 {stats['n']} 次, 累计权重 {stats['tw']:.1f}")

    print("\n" + "=" * 78)
    print("回测完成。")
    print("=" * 78)


# ===================================================================
#  参数敏感性
# ===================================================================

def sensitivity_analysis(start_date: str = '2019-04-01'):
    """参数敏感性分析。"""
    configs = [
        ('激进 (无限制)', {}),
        ('平滑 min_hold=3', {'min_hold_days': 3, 'max_turnover': 0.3}),
        ('保守 min_hold=5', {'min_hold_days': 5, 'max_turnover': 0.2}),
        ('低换手 max=0.2', {'min_hold_days': 1, 'max_turnover': 0.2}),
        ('高换手 max=0.8', {'min_hold_days': 1, 'max_turnover': 0.8}),
    ]

    results = []
    for label, cfg in configs:
        r = run_backtest(start_date=start_date, params=cfg)
        perf = evaluate_performance(r)
        results.append({
            'config': label,
            'CAGR': perf['strategy_cagr'],
            'MaxDD': perf['max_dd'],
            'Sharpe': perf['sharpe'],
            'IR': perf['information_ratio'],
            'Calmar': perf['calmar'],
            'WinRate': perf['win_rate'],
            'N_Trades': len(r['trades']),
            'Turnover': perf['total_turnover'],
            'TotalRet': perf['strategy_total_ret'],
        })

    df = pd.DataFrame(results).set_index('config')
    print("\n--- 参数敏感性 ---")
    print(df.to_string(
        float_format=lambda x: f'{x:8.2f}' if isinstance(x, float) else str(x)))
    return df


# ===================================================================
#  绘图
# ===================================================================

def plot_backtest(result: dict, out_dir: str = 'output'):
    """生成回测图表。"""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False
    except ImportError:
        print("  matplotlib 不可用，跳过绘图")
        return

    os.makedirs(out_dir, exist_ok=True)

    nav = result['nav'].dropna()
    bench = result['benchmark_nav']
    cash = result['cash_nav']
    riskoff = result['riskoff_nav']
    perf = evaluate_performance(result)

    fmt = mdates.DateFormatter('%Y-%m')
    yr_loc = mdates.YearLocator(1)

    # 图 1: 净值曲线
    fig, ax = plt.subplots(1, 1, figsize=(16, 6))
    ax.plot(nav.index, nav.values, label='多主题轮动策略', linewidth=1.3, color='#2196F3')
    ax.plot(bench.index, bench.values, label='沪深300', linewidth=1.0, color='#9E9E9E',
            linestyle='--', alpha=0.6)
    ax.plot(cash.index, cash.values, label='现金(1.0)', linewidth=0.8, color='#BDBDBD',
            linestyle=':', alpha=0.5)
    ax.plot(riskoff.index, riskoff.values, label='风险off(512890仅持仓)', linewidth=0.8,
            color='#FF9800', linestyle='-.', alpha=0.6)
    ax.axhline(y=1.0, color='#CCCCCC', linewidth=0.5, alpha=0.5)
    ax.set_title(
        f'多主题轮动回测净值曲线\n'
        f'策略CAGR={perf["strategy_cagr"]:+.1%} | BH CAGR={perf["bench_cagr"]:+.1%} | '
        f'MaxDD={perf["max_dd"]:.1%} | Sharpe={perf["sharpe"]:.1f} | '
        f'IR={perf["information_ratio"]:.1f}',
        fontsize=11, fontweight='bold')
    ax.legend(fontsize=9, loc='upper left')
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(fmt)
    ax.xaxis.set_major_locator(yr_loc)
    ax.tick_params(axis='x', rotation=30, labelsize=8)
    ax.set_ylabel('累计净值')
    plt.tight_layout()
    plt.savefig(f'{out_dir}/backtest_nav.png', dpi=150, bbox_inches='tight')
    print(f"  saved: {out_dir}/backtest_nav.png")
    plt.close()

    # 图 2: 回撤曲线
    fig, ax = plt.subplots(1, 1, figsize=(16, 4))
    rolling_max = nav.cummax()
    dd = (nav - rolling_max) / (rolling_max + 1e-12)
    ax.fill_between(dd.index, dd.values, 0, color='#E74C3C', alpha=0.3)
    ax.plot(dd.index, dd.values, color='#C0392B', linewidth=0.8)
    ax.set_title('策略回撤曲线 (MaxDD = {:.1%})'.format(perf['max_dd']),
                 fontsize=11, fontweight='bold')
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(fmt)
    ax.xaxis.set_major_locator(yr_loc)
    ax.tick_params(axis='x', rotation=30, labelsize=8)
    ax.set_ylabel('回撤')
    plt.tight_layout()
    plt.savefig(f'{out_dir}/backtest_drawdown.png', dpi=150, bbox_inches='tight')
    print(f"  saved: {out_dir}/backtest_drawdown.png")
    plt.close()

    # 图 3: 持仓权重 (堆叠面积)
    all_codes_sorted = sorted({
        code for t in result['trades']
        for code in t.get('target_codes', []) if code
    })
    if all_codes_sorted:
        # 预建 date → trade 索引
        trade_by_date = {t['date']: t for t in result['trades']}
        mat = {c: {} for c in all_codes_sorted}
        last_t = None
        for d in nav.index:
            if d in trade_by_date:
                last_t = trade_by_date[d]
            if last_t:
                for code, w in last_t['target_weights'].items():
                    mat.setdefault(code, {})[d] = w
            else:
                for code in all_codes_sorted:
                    mat.setdefault(code, {})[d] = 0

        weight_df = pd.DataFrame(mat)
        if not weight_df.empty:
            fig, ax = plt.subplots(1, 1, figsize=(16, 5))
            color_map = {
                '512000': '#FF9800', '512880': '#FF5722',
                '513180': '#4CAF50', '513130': '#8BC34A',
                '159992': '#2196F3', '515120': '#03A9F4', '513120': '#3F51B5',
                '159516': '#9C27B0', '512890': '#795548',
            }
            ordered_codes = [c for c in ['512890', '512000', '512880', '513180', '513130',
                                          '159992', '515120', '513120', '159516']
                             if c in weight_df.columns]
            if ordered_codes:
                weight_df[ordered_codes].T.plot(ax=ax, kind='area', stacked=True,
                                                 colormap=plt.cm.Set3, legend=False)
                for code in ordered_codes:
                    if code in weight_df.columns:
                        ax.axhline(y=0, color='white', linewidth=0.5)
                ax.set_title('每日持仓权重 (堆叠面积图)', fontsize=11, fontweight='bold')
                ax.grid(True, alpha=0.3)
                ax.xaxis.set_major_formatter(fmt)
                ax.xaxis.set_major_locator(yr_loc)
                ax.tick_params(axis='x', rotation=30, labelsize=8)
                ax.set_ylabel('权重')
                ax.set_ylim(0, 1.05)
                plt.tight_layout()
                plt.savefig(f'{out_dir}/backtest_weights.png', dpi=150,
                            bbox_inches='tight')
                print(f"  saved: {out_dir}/backtest_weights.png")
                plt.close()

    # 图 4: 分析 (日收益分布 / 年化收益 / 月度热力 / 滚动夏普)
    valid_rets = result['daily_rets'].dropna()
    if len(valid_rets) > 10:
        fig, axes = plt.subplots(2, 2, figsize=(16, 8))
        axes = axes.flatten()

        axes[0].hist(valid_rets, bins=80, edgecolor='black', alpha=0.7, color='#2196F3')
        axes[0].axvline(x=0, color='red', linestyle='--')
        axes[0].set_title('日收益率分布')
        axes[0].set_xlabel('日收益率')
        axes[0].grid(True, alpha=0.3)

        ann_rets = valid_rets * 252
        axes[1].hist(ann_rets, bins=80, edgecolor='black', alpha=0.7, color='#4CAF50')
        axes[1].axvline(x=0, color='red', linestyle='--')
        axes[1].set_title('年化日收益率分布')
        axes[1].set_xlabel('年化收益率')
        axes[1].grid(True, alpha=0.3)

        vr_date = valid_rets.to_frame('ret')
        vr_date['year'] = vr_date.index.year
        vr_date['month'] = vr_date.index.month
        monthly = vr_date.groupby(['year', 'month'])['ret'].agg(
            total=lambda x: (1 + x).prod() - 1,
        )
        if not monthly.empty:
            mp = monthly['total'].unstack()
            ax3 = axes[2]
            im = ax3.imshow(mp.values, cmap='RdYlGn', vmin=-0.1, vmax=0.1, aspect='auto')
            ax3.set_xticks(range(len(mp.columns)))
            ax3.set_yticks(range(len(mp.index)))
            ax3.set_xticklabels(mp.columns)
            ax3.set_yticklabels(mp.index)
            ax3.set_title('月度收益热力 (%)')
            plt.colorbar(im, ax=ax3)

        window = 60
        if len(valid_rets) > window:
            rs = (valid_rets.rolling(window).mean()
                  / valid_rets.rolling(window).std() * np.sqrt(252))
            axes[3].plot(rs.index, rs.values, label='滚动 60d 夏普',
                         color='#2196F3', linewidth=1)
            axes[3].axhline(y=0, color='red', linestyle='--')
            axes[3].set_title('滚动 60 日夏普 (年化)')
            axes[3].legend(fontsize=8)
            axes[3].grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(f'{out_dir}/backtest_analysis.png', dpi=150, bbox_inches='tight')
        print(f"  saved: {out_dir}/backtest_analysis.png")
        plt.close()

    print(f"\n  图表已保存至 {out_dir}/")


# ===================================================================
#  主函数
# ===================================================================

def main():
    """完整回测流程。"""
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    print("=" * 78)
    print("多主题轮动策略 -- Step 3 回测引擎")
    print("=" * 78)

    print("\n[1] 运行回测 (start_date='2019-04-01')...")
    result = run_backtest(start_date='2019-04-01')

    print_report(result)

    print("\n[2] 参数敏感性分析...")
    sensitivity_analysis(start_date='2019-04-01')

    print("\n[3] 生成图表...")
    plot_backtest(result, out_dir='output')

    if result['trades']:
        pd.DataFrame(result['trades']).to_csv(
            'output/backtest_trades.csv', index=False)
        print(f"  saved: output/backtest_trades.csv "
              f"({len(result['trades'])} 笔交易)")

    print("\n" + "=" * 78)
    print("回测完成。")
    print("=" * 78)


if __name__ == '__main__':
    main()
