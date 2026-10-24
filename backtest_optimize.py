"""
ETF 双因子量化回测与参数寻优 v4 — 最终版
===========================================
"""

import sqlite3
import pandas as pd
import numpy as np
import warnings
import optuna
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import os, sys, io, math

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

DB = 'etf.db'

# ──────────────────────────────────────────────
# 0. 数据加载
# ──────────────────────────────────────────────
def load_etf_data(codes):
    conn = sqlite3.connect(DB)
    data = {}
    for code in codes:
        scale = pd.read_sql(f"SELECT date, shares FROM fund_scale WHERE code='{code}'", conn)
        price = pd.read_sql(f"SELECT date, close FROM fund_price WHERE code='{code}'", conn)
        df = scale.merge(price, on='date', how='inner').sort_values('date').reset_index(drop=True)
        df['date_dt'] = pd.to_datetime(df['date'])
        data[code] = df
    conn.close()
    return data

# ──────────────────────────────────────────────
# 1. 因子计算 (EMA 自适应去趋势)
# ──────────────────────────────────────────────
def compute_factors(df: pd.DataFrame, N: int, M: int):
    shares = df['shares']
    closes = df['close']

    log_shares = np.log(shares.replace(0, np.nan))
    share_pct = log_shares.diff()

    ewm_mean = share_pct.ewm(span=N, min_periods=N).mean()
    ewm_std = share_pct.ewm(span=N, min_periods=N).std()
    share_z = (share_pct - ewm_mean) / ewm_std.replace(0, np.nan)

    close_ma = closes.rolling(M, min_periods=M).mean()
    close_std = closes.rolling(M, min_periods=M).std()
    bias_z = (closes - close_ma) / close_std.replace(0, np.nan)

    df['share_z'] = share_z
    df['bias_z'] = bias_z
    return df

# ──────────────────────────────────────────────
# 2. 回测引擎
# ──────────────────────────────────────────────
def backtest(df, params):
    tz = params['threshold_z']
    tb = params['threshold_bias']
    hold_td = params['hold_days']
    sl = params['stop_loss']
    tp = params['take_profit']

    sz = df['share_z'].values
    bz = df['bias_z'].values
    close = df['close'].values
    dates = df['date_dt'].values
    n = len(close)

    in_pos = False
    entry_price = 0.0
    entry_bar = 0
    td_in_pos = 0

    nav = np.ones(n)
    nav[0] = 1.0
    trades = []

    for i in range(1, n):
        if in_pos:
            td_in_pos += 1
            ret = (close[i] - entry_price) / entry_price

            if ret <= -sl:
                nav[i] = nav[i-1] * (1 + ret)
                trades.append({'entry_date': str(dates[entry_bar]), 'exit_date': str(dates[i]),
                               'entry_price': entry_price, 'exit_price': close[i],
                               'ret': ret, 'reason': 'stop_loss', 'hold_trading_days': td_in_pos})
                in_pos = False; td_in_pos = 0
            elif ret >= tp:
                nav[i] = nav[i-1] * (1 + ret)
                trades.append({'entry_date': str(dates[entry_bar]), 'exit_date': str(dates[i]),
                               'entry_price': entry_price, 'exit_price': close[i],
                               'ret': ret, 'reason': 'take_profit', 'hold_trading_days': td_in_pos})
                in_pos = False; td_in_pos = 0
            elif hold_td > 0 and td_in_pos >= hold_td:
                nav[i] = nav[i-1] * (1 + ret)
                trades.append({'entry_date': str(dates[entry_bar]), 'exit_date': str(dates[i]),
                               'entry_price': entry_price, 'exit_price': close[i],
                               'ret': ret, 'reason': 'hold_expiry', 'hold_trading_days': td_in_pos})
                in_pos = False; td_in_pos = 0
            else:
                nav[i] = nav[i-1] * (1 + ret)
        else:
            if not np.isnan(sz[i]) and not np.isnan(bz[i]) and sz[i] > tz and bz[i] < tb:
                in_pos = True; entry_price = close[i]; entry_bar = i; td_in_pos = 0
                nav[i] = nav[i-1]

    if not trades:
        return {'cagr': 0.0, 'max_dd': 0.0, 'sharpe': 0.0, 'win_rate': 0.0,
                'n_trades': 0, 'nav': nav, 'trades': trades, 'trade_rets': []}

    trade_rets = [t['ret'] for t in trades]
    nav_values = [1.0]
    for r in trade_rets:
        nav_values.append(nav_values[-1] * (1 + r))
    trade_nav = np.array(nav_values)

    total_days = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days
    years = total_days / 252.0
    cagr = float(trade_nav[-1] ** (1.0 / years) - 1.0) if years > 0 else 0.0

    running_max = np.maximum.accumulate(trade_nav)
    dd = (trade_nav - running_max) / (running_max + 1e-12)
    max_dd = float(abs(dd.min()))

    if len(trade_rets) > 2:
        avg_ret = np.mean(trade_rets)
        std_ret = np.std(trade_rets, ddof=1)
        avg_hold = np.mean([t['hold_trading_days'] for t in trades])
        tp_year = 252.0 / (avg_hold + 1e-6)
        sharpe = float((avg_ret - 0.025/252) / (std_ret + 1e-12) * np.sqrt(tp_year))
    elif len(trade_rets) == 2:
        # 2 trades: report simple return, don't report misleading sharpe
        avg_ret = np.mean(trade_rets)
        sharpe = float(avg_ret * np.sqrt(252 / 10))  # rough annualization
    else:
        sharpe = 0.0

    win_rate = float(np.mean([r > 0 for r in trade_rets]))

    return {'cagr': cagr, 'max_dd': max_dd, 'sharpe': sharpe,
            'win_rate': win_rate, 'n_trades': len(trades),
            'nav': nav, 'trades': trades, 'trade_rets': trade_rets,
            'trade_nav': trade_nav}

# ──────────────────────────────────────────────
# 3. Optuna 目标函数
# ──────────────────────────────────────────────
def objective(trial, df_raw, param_space):
    N = trial.suggest_int('N', param_space['N'][0], param_space['N'][1])
    M = trial.suggest_int('M', param_space['M'][0], param_space['M'][1])
    tz = trial.suggest_float('threshold_z', param_space['threshold_z'][0], param_space['threshold_z'][1])
    tb = trial.suggest_float('threshold_bias', param_space['threshold_bias'][0], param_space['threshold_bias'][1])
    hold_days = trial.suggest_int('hold_days', param_space['hold_days'][0], param_space['hold_days'][1])
    sl = trial.suggest_float('stop_loss', param_space['stop_loss'][0], param_space['stop_loss'][1])
    tp = trial.suggest_float('take_profit', param_space['take_profit'][0], param_space['take_profit'][1])

    params = {'N': N, 'M': M, 'threshold_z': tz, 'threshold_bias': tb,
              'hold_days': hold_days, 'stop_loss': sl, 'take_profit': tp}
    df_work = compute_factors(df_raw.copy(), N=N, M=M)
    res = backtest(df_work, params)

    score = res['sharpe'] * 0.35 + res['cagr'] * 0.30 - res['max_dd'] * 0.25 + res['win_rate'] * 0.10
    # 交易次数惩罚: 少于3次大幅惩罚, 少于5次中等惩罚
    if res['n_trades'] < 3: score -= 5.0
    elif res['n_trades'] < 5: score -= 2.0
    return score

# ──────────────────────────────────────────────
# 4. 事件研究
# ──────────────────────────────────────────────
def event_study(df, threshold=2.0):
    sz = df['share_z'].values
    close = df['close'].values
    event_idx = np.where(~np.isnan(sz) & (sz > threshold))[0]

    rows = []
    for h in [1, 3, 5, 10]:
        rets = []
        for idx in event_idx:
            end = min(idx + h, len(close) - 1)
            if close[idx] > 0 and close[end] > 0:
                rets.append((close[end] - close[idx]) / close[idx])
        if rets:
            rows.append({'horizon': f'{h}日',
                         'avg_return': np.mean(rets) * 100,
                         'median_return': np.median(rets) * 100,
                         'up_prob': np.mean([r > 0 for r in rets]) * 100,
                         'n_events': len(rets)})
    return pd.DataFrame(rows)

# ──────────────────────────────────────────────
# 5. 分组参数
# ──────────────────────────────────────────────
GROUPS = {
    '港股科技组': {
        'codes': ['513180', '513130'],
        'param_space': {'N': (10, 25), 'M': (10, 30), 'threshold_z': (1.5, 2.5),
                        'threshold_bias': (-2.5, -1.0), 'hold_days': (3, 15),
                        'stop_loss': (0.03, 0.08), 'take_profit': (0.05, 0.20)}
    },
    '医药/高波科技组': {
        'codes': ['515120', '159516'],
        'param_space': {'N': (10, 30), 'M': (10, 30), 'threshold_z': (1.2, 2.3),
                        'threshold_bias': (-2.5, -1.0), 'hold_days': (3, 20),
                        'stop_loss': (0.03, 0.08), 'take_profit': (0.05, 0.20)}
    },
    '券商/金融组': {
        'codes': ['512000', '512880'],
        'param_space': {'N': (10, 35), 'M': (10, 30), 'threshold_z': (1.2, 2.5),
                        'threshold_bias': (-2.5, -1.0), 'hold_days': (3, 15),
                        'stop_loss': (0.02, 0.08), 'take_profit': (0.03, 0.20)}
    },
    '红利/策略组': {
        'codes': ['512890'],
        'param_space': {'N': (20, 50), 'M': (20, 60), 'threshold_z': (1.0, 2.0),
                        'threshold_bias': (-2.5, -0.8), 'hold_days': (5, 40),
                        'stop_loss': (0.02, 0.06), 'take_profit': (0.03, 0.15)}
    },
}

# ──────────────────────────────────────────────
# 6. 主流程
# ──────────────────────────────────────────────
def main():
    all_codes = sorted(set(c for g in GROUPS.values() for c in g['codes']))

    print(f"[1/4] 加载 {len(all_codes)} 只 ETF 数据...\n")
    data = load_etf_data(all_codes)
    for code, df in data.items():
        print(f"    {code}: {len(df)} 行, {df['date_dt'].iloc[0].date()} → {df['date_dt'].iloc[-1].date()}")

    # ── [2/4] Optuna 寻优 ──
    print(f"\n[2/4] 开始 Optuna 参数寻优 (每只 ETF 200 trials)...\n")
    results = {}

    for group_name, group_data in GROUPS.items():
        print(f"{'='*70}")
        print(f"  组别: {group_name}")
        print(f"{'='*70}")

        for code in group_data['codes']:
            df = data[code]
            ps = group_data['param_space']

            # 信号密度预览
            for N_test, M_test in [(20, 20), (20, 40), (30, 30)]:
                df_t = compute_factors(df.copy(), N=N_test, M=M_test)
                for tz_test in [1.0, 1.5, 2.0, 2.5]:
                    for tb_test in [-1.0, -1.5, -2.0, -2.5]:
                        sigs = ((df_t['share_z'] > tz_test) & (df_t['bias_z'] < tb_test)).sum()
                        if 0 < sigs <= 40:
                            print(f"    [{code}] N={N_test} M={M_test} z>{tz_test} b<{tb_test}: {sigs} 信号")

            print(f"\n  [{code}] Optuna 寻优 200 trials...")
            study = optuna.create_study(direction='maximize',
                sampler=optuna.samplers.TPESampler(seed=42),
                study_name=f'{code}_opt_final')
            study.optimize(lambda trial: objective(trial, df, ps), n_trials=200, show_progress_bar=False)

            bp = study.best_params
            df_best = compute_factors(df.copy(), N=bp['N'], M=bp['M'])
            res = backtest(df_best, bp)
            res['group'] = group_name
            res['best_params'] = bp
            results[code] = res

            bh = df['close'].iloc[-1] / df['close'].iloc[0] - 1
            print(f"    最优得分: {study.best_value:.4f}")
            print(f"    参数: N={bp['N']}, M={bp['M']}, z>{bp['threshold_z']:.2f}, "
                  f"b<{bp['threshold_bias']:.2f}, hold={bp['hold_days']}d, "
                  f"sl={bp['stop_loss']:.0%}, tp={bp['take_profit']:.0%}")
            print(f"    绩效: CAGR={res['cagr']:+.2%}, MaxDD={res['max_dd']:.2%}, "
                  f"Sharpe={res['sharpe']:.1f}, WR={res['win_rate']:.0%}, "
                  f"交易={res['n_trades']}次, NAV={res['trade_nav'][-1]:.3f}")
            print(f"    vs BH: {bh:+.2%} | 相对BH: {(res['cagr'] - bh) if abs(bh) > 0.01 else res['trade_nav'][-1]-1-bh:+.2%}")

            # 事件研究
            for thresh in [1.5, 2.0]:
                es = event_study(df_best, threshold=thresh)
                if not es.empty:
                    print(f"\n    事件研究 (share_z > {thresh}):")
                    for _, row in es.iterrows():
                        print(f"      {row['horizon']}: 平均 {row['avg_return']:+.2f}% | 中位数 {row['median_return']:+.2f}% | "
                              f"上涨率 {row['up_prob']:.1f}% | 事件数 {row['n_events']}")

    # ── [3/4] 汇总 ──
    print(f"\n{'='*80}")
    print("【汇总对比表】双因子策略 vs 买入持有 (2019-2026)")
    print(f"{'='*80}")

    rows = []
    for code, res in sorted(results.items()):
        df = data[code]
        bh = df['close'].iloc[-1] / df['close'].iloc[0] - 1
        rows.append({
            '代码': code, '组别': res['group'],
            '策略NAV': f"{res['trade_nav'][-1]:.3f} ({res['trade_nav'][-1]-1:+.1%})",
            'BH回报': f"{bh:+.1%}",
            '超额': f"{res['trade_nav'][-1] - 1 - bh:+.1%}",
            'CAGR': f"{res['cagr']:+.2%}",
            'MaxDD': f"{res['max_dd']:.2%}",
            'Sharpe': f"{res['sharpe']:.1f}",
            '胜率': f"{res['win_rate']:.0%}",
            '交易数': res['n_trades'],
        })

    print(pd.DataFrame(rows).to_string(index=False))

    # ── [4/4] 绘图 ──
    print(f"\n[4/4] 生成图表...")
    os.makedirs('output', exist_ok=True)

    # 图1: 累计收益对比 (交易复利 vs 买入持有) + 买卖标记
    fig, axes = plt.subplots(2, 4, figsize=(24, 10))
    axes = axes.flatten()

    for idx, (code, res) in enumerate(sorted(results.items())):
        ax = axes[idx]
        df = data[code]
        bh_ret = (df['close'] / df['close'].iloc[0]) - 1

        # 构建日频交易净值
        strat_nav = np.ones(len(df))
        if res['trades']:
            trade_dates = [pd.Timestamp(t['exit_date']) for t in res['trades']]
            trade_rets_list = [t['ret'] for t in res['trades']]
            nav_val = 1.0
            ti = 0
            for i in range(len(df)):
                if ti < len(trade_rets_list):
                    if df['date_dt'].iloc[i] >= trade_dates[ti]:
                        nav_val *= (1 + trade_rets_list[ti])
                        ti += 1
                strat_nav[i] = nav_val

        strat_ret = strat_nav - 1

        # 在累计收益图上标注买卖点
        if res['trades']:
            buy_dates = []
            buy_nav = []
            sell_dates = []
            sell_nav = []
            for t in res['trades']:
                entry_dt = pd.Timestamp(t['entry_date'])
                exit_dt = pd.Timestamp(t['exit_date'])
                entry_price = t['entry_price']

                # 找到 entry 在 df 中的位置
                entry_idx = df[df['date_dt'] >= entry_dt].index.min()
                exit_idx = df[df['date_dt'] >= exit_dt].index.min()

                # 买入点 (绿色三角形)
                buy_dates.append(entry_dt)
                buy_nav.append(strat_ret[entry_idx - 1] if entry_idx > 0 else 0)

                # 卖出点 (红色倒三角形)
                sell_dates.append(exit_dt)
                sell_nav.append(strat_ret[exit_idx])

                # 在买入和卖出之间画一条淡绿色的连接线表示持仓区间
                # 用水平线在累计收益上标示持仓段
                if entry_idx < exit_idx:
                    ax.axvspan(entry_dt, exit_dt, alpha=0.12, color='green')

            # 买入标记
            ax.scatter(buy_dates, buy_nav, marker='^', s=120, c='#27AE60',
                       edgecolors='white', linewidths=1.5, zorder=5, label='买入')
            # 卖出标记
            ax.scatter(sell_dates, sell_nav, marker='v', s=120, c='#E74C3C',
                       edgecolors='white', linewidths=1.5, zorder=5, label='卖出')

            # 添加交易编号标签
            for i, t in enumerate(res['trades']):
                entry_idx = df[df['date_dt'] >= pd.Timestamp(t['entry_date'])].index.min()
                exit_idx = df[df['date_dt'] >= pd.Timestamp(t['exit_date'])].index.min()
                ax.annotate(f'#{i+1} {t["ret"]:+.1%}',
                           xy=(pd.Timestamp(t['exit_date']), strat_ret[exit_idx]),
                           xytext=(5, -10), textcoords='offset points',
                           fontsize=5, color='#E74C3C', fontweight='bold')

        # 格式化标题
        cagr_str = f"{res['cagr']:+.1%}" if abs(res['cagr']) > 0.005 else f"{res['trade_nav'][-1]-1:+.1%}"
        ax.plot(df['date_dt'], strat_ret, label='双因子策略', linewidth=1.3, color='#2196F3', alpha=0.8)
        ax.plot(df['date_dt'], bh_ret, label='买入持有', linewidth=1.0, color='#9E9E9E', linestyle='--', alpha=0.6)
        ax.axhline(y=0, color='#CCCCCC', linewidth=0.5)

        title = (f"{code} ({res['group']})\n"
                f"策略 NAV={res['trade_nav'][-1]:.3f} | BH={bh:+.1%}\n"
                f"CAGR={cagr_str} | MaxDD={res['max_dd']:.1%} | Sharpe={res['sharpe']:.1f}")
        ax.set_title(title, fontsize=9, fontweight='bold')
        ax.legend(fontsize=7, loc='upper left')
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax.xaxis.set_major_locator(mdates.YearLocator(2))
        ax.tick_params(axis='x', rotation=30, labelsize=7)
        ax.set_ylabel('累计收益')
        y_min = min(strat_ret.min(), bh_ret.min()) * 1.1
        y_max = max(strat_ret.max(), bh_ret.max()) * 1.1 if abs(max(strat_ret.max(), bh_ret.max())) > 0.05 else 0.3
        ax.set_ylim(y_min, y_max)

    for i in range(len(results), 8):
        axes[i].axis('off')

    plt.suptitle('双因子策略 vs 买入持有 — 累计收益对比 (2019-2026)',
                 fontsize=14, fontweight='bold', y=1.02)
    plt.tight_layout()
    plt.savefig('output/equity_curve_final.png', dpi=150, bbox_inches='tight')
    print("    saved: output/equity_curve_final.png")
    plt.close()

    # 图2: 红利低波因子时序
    fig, axes = plt.subplots(3, 1, figsize=(16, 12))

    bp_rb = results['512890']['best_params']
    df_rb = compute_factors(data['512890'].copy(), N=bp_rb['N'], M=bp_rb['M'])

    axes[0].plot(df_rb['date_dt'], df_rb['close'], color='#333', linewidth=1.0)
    axes[0].set_title('512890 红利低波 — 价格走势 (2019-2026)', fontsize=11, fontweight='bold')
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(df_rb['date_dt'], df_rb['share_z'], color='#2196F3', linewidth=0.8)
    axes[1].axhline(y=bp_rb['threshold_z'], color='red', linestyle='--', alpha=0.5,
                    label=f"threshold_z = {bp_rb['threshold_z']:.2f}")
    axes[1].set_title(f'份额因子 share_z (EMA, N={bp_rb["N"]})', fontsize=10)
    axes[1].legend(fontsize=8)
    axes[1].grid(True, alpha=0.3)

    axes[2].plot(df_rb['date_dt'], df_rb['bias_z'], color='#E91E63', linewidth=0.8)
    axes[2].axhline(y=bp_rb['threshold_bias'], color='red', linestyle='--', alpha=0.5,
                    label=f"threshold_bias = {bp_rb['threshold_bias']:.2f}")
    axes[2].set_title(f'价格偏离因子 bias_z (M={bp_rb["M"]})', fontsize=10)
    axes[2].legend(fontsize=8)
    axes[2].grid(True, alpha=0.3)
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))

    plt.suptitle('512890 红利低波 — 因子时序分解 (最优参数: N=36, M=53)',
                 fontsize=13, fontweight='bold', y=1.02)
    plt.tight_layout()
    plt.savefig('output/512890_factors_final.png', dpi=150, bbox_inches='tight')
    print("    saved: output/512890_factors_final.png")
    plt.close()

    # 图3: 事件研究 (所有组)
    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    axes = axes.flatten()

    group_map = {}
    for g, gd in GROUPS.items():
        for c in gd['codes']:
            if c in results: group_map.setdefault(g, []).append(c)

    colors = ['#2196F3', '#4CAF50', '#FF9800', '#F44336']
    for idx, (gname, codes) in enumerate(group_map.items()):
        ax = axes[idx]
        for i, code in enumerate(codes):
            if code in results:
                bp = results[code]['best_params']
                df_f = compute_factors(data[code].copy(), N=bp['N'], M=bp['M'])
                es = event_study(df_f, threshold=2.0)
                if not es.empty:
                    ax.plot(es['horizon'], es['avg_return'], 'o-',
                           label=f"{code}", color=colors[i % len(colors)],
                           linewidth=2, markersize=8, markerfacecolor='white', markeredgewidth=2)
        ax.set_title(f'{gname} — 份额暴量申购后短期收益 (share_z > 2.0)', fontsize=11, fontweight='bold')
        ax.set_xlabel('持仓期', fontsize=9)
        ax.set_ylabel('平均收益率 (%)', fontsize=9)
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.tick_params(axis='x', rotation=30)
        ax.axhline(y=0, color='black', linewidth=0.5)

    for i in range(len(group_map), 4):
        axes[i].axis('off')

    plt.suptitle('事件研究: 份额暴量申购 (share_z > 2.0) 后短期收益', fontsize=13, fontweight='bold', y=1.02)
    plt.tight_layout()
    plt.savefig('output/event_study_final.png', dpi=150, bbox_inches='tight')
    print("    saved: output/event_study_final.png")
    plt.close()

    # 保存数据
    all_trades = []
    for code, res in sorted(results.items()):
        for t in res['trades']:
            t['code'] = code
            t['group'] = res['group']
        all_trades.extend(res['trades'])

    if all_trades:
        pd.DataFrame(all_trades).to_csv('output/trades_final.csv', index=False)
        print(f"    saved: output/trades_final.csv ({len(all_trades)} 笔交易)")

    param_rows = []
    for code, res in sorted(results.items()):
        p = res['best_params']
        df = data[code]
        bh = df['close'].iloc[-1] / df['close'].iloc[0] - 1
        param_rows.append({
            'code': code, 'group': res['group'],
            'N': p['N'], 'M': p['M'],
            'threshold_z': round(p['threshold_z'], 3),
            'threshold_bias': round(p['threshold_bias'], 3),
            'hold_days': p['hold_days'],
            'stop_loss': round(p['stop_loss'], 3),
            'take_profit': round(p['take_profit'], 3),
            'cagr': round(res['cagr'], 4),
            'max_dd': round(res['max_dd'], 4),
            'sharpe': round(res['sharpe'], 2),
            'win_rate': round(res['win_rate'], 3),
            'n_trades': res['n_trades'],
            'trade_nav': round(res['trade_nav'][-1], 4),
            'bh_return': round(bh, 4),
            'excess': round(res['trade_nav'][-1] - 1 - bh, 4),
        })
    pd.DataFrame(param_rows).to_csv('output/summary_final.csv', index=False)
    print("    saved: output/summary_final.csv")

    print("\n[*] 全部完成!")

if __name__ == '__main__':
    main()
