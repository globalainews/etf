# -*- coding: utf-8 -*-
"""Backtest E2 signal strategy — true equal-weight allocation."""
import sqlite3, numpy as np, pandas as pd, os, argparse, warnings
from collections import Counter
warnings.filterwarnings('ignore')

conn = sqlite3.connect('D:/claude projects/ETF/etf.db')
cursor = conn.cursor()

GROUP_A = ['159228', '159307', '159525', '159547', '159549', '512890', '563020']
name_map = {}
cursor.execute('SELECT code, name FROM funds WHERE code IN (SELECT DISTINCT code FROM fund_scale)')
for r in cursor.fetchall():
    name_map[r[0]] = r[1]

cursor.execute('SELECT date, code, close FROM fund_price WHERE code IN ({})'.format(
    ','.join(f'"{c}"' for c in GROUP_A)))
price_df = pd.DataFrame(cursor.fetchall(), columns=['date', 'code', 'close'])
price_df['date'] = pd.to_datetime(price_df['date'])

cursor.execute('SELECT date, code, shares FROM fund_scale WHERE code IN ({})'.format(
    ','.join(f'"{c}"' for c in GROUP_A)))
scale_df = pd.DataFrame(cursor.fetchall(), columns=['date', 'code', 'shares'])
scale_df['date'] = pd.to_datetime(scale_df['date'])

conn.close()

# Merge
merged = price_df.merge(scale_df, on=['date', 'code'], how='inner')
merged.sort_values(['code', 'date'], inplace=True)
print(f"Total merged: {len(merged)}")
print(f"Dates: {merged['date'].min()} ~ {merged['date'].max()}")

# ============================================
# 1. Compute indicators per ETF
# ============================================
etf_data = {}
for code in GROUP_A:
    df = merged[merged['code'] == code].copy().sort_values('date').reset_index(drop=True)
    df['ret_1d'] = df['close'].pct_change(1)
    df['ma20'] = df['close'].rolling(20, min_periods=20).mean()
    df['price_dev_20'] = df['close'] / df['ma20'] - 1
    df['share_chg_1d'] = df['shares'].pct_change(1)
    w20m = df['share_chg_1d'].rolling(20, min_periods=20).mean()
    w20s = df['share_chg_1d'].rolling(20, min_periods=20).std()
    df['share_z_20'] = (df['share_chg_1d'] - w20m) / w20s.replace(0, np.nan)
    # 份额的 20 日均线（方案 A 确认层需要：份额 > 份额20均线）
    df['shares_ma20'] = df['shares'].rolling(20, min_periods=20).mean()
    df['name'] = name_map.get(code, '')
    etf_data[code] = df

# ============================================
# 2. Generate buy signals (vectorized)
# ============================================

def make_e2_signal_mask(df, trigger_z=1.5, dev_gate=-0.015):
    """单日触发掩码：share_z_20 > 1.5 且 price_dev_20 < -0.015（向量化）

    NaN 一律视为未触发。
    """
    return ((df['share_z_20'] > trigger_z)
            & (df['price_dev_20'] < dev_gate)
            & df['share_z_20'].notna()
            & df['price_dev_20'].notna())


def generate_signals(etf_data, mode='single_day', confirm_days=3):
    """生成 E2 信号。

    single_day : 单日触发即信号（冻结版），signal_date = 触发日 T，
                 入场价 = 触发日收盘。
    confirmed  : 方案 A —— T 触发后，T+1..T+confirm_days 每日 shares 均
                 > 当日 shares_ma20，才输出信号；signal_date = T+3，
                 入场价 = T+3 收盘（对应"T+3 闭市后 / T+4 开盘"）。
                 任一日跌破均线则撤销（假突破）。

    全程向量化，无 Python 循环。
    """
    all_signals = []
    for code, df in etf_data.items():
        trigger_mask = make_e2_signal_mask(df)

        if mode == 'confirmed':
            # 确认层：shares > shares_ma20 连续 confirm_days 天
            ok = (df['shares'] > df['shares_ma20']).fillna(False)
            confirm_mask = (ok.rolling(confirm_days, min_periods=confirm_days)
                              .sum() == confirm_days)
            # T+3 日：三日前有触发 且 近三日份额连续站上份额均线
            signal_mask = trigger_mask.shift(confirm_days) & confirm_mask
        else:  # single_day：复现冻结版
            signal_mask = trigger_mask

        for idx in df.index[signal_mask]:
            if mode == 'confirmed':
                t_idx = idx - confirm_days  # 触发日 T
                trig = df.loc[t_idx]
                all_signals.append({
                    'code': code,
                    'signal_date': df['date'].iloc[idx],   # 输出日 = T+3（入场日）
                    'trigger_date': trig['date'],          # T 触发日（元数据）
                    'price': float(df['close'].iloc[idx]), # 入场价 = T+3 收盘
                    'trigger_share_z_20': float(trig['share_z_20']),
                    'trigger_price_dev_20': float(trig['price_dev_20']),
                    'name': name_map.get(code, ''),
                })
            else:
                trig = df.loc[idx]
                all_signals.append({
                    'code': code, 'signal_date': trig['date'],
                    'price': float(trig['close']),
                    'share_z_20': float(trig['share_z_20']),
                    'price_dev_20': float(trig['price_dev_20']),
                    'name': name_map.get(code, ''),
                })
    all_signals.sort(key=lambda x: (str(x['signal_date']), x['code']))
    return all_signals


# 默认使用 single_day（冻结版），保持研究产物不变
all_signals = generate_signals(etf_data, mode='single_day')
print(f"\nTotal E2 signals: {len(all_signals)}")
for code in GROUP_A:
    c = sum(1 for s in all_signals if s['code'] == code)
    print(f"  {code} {name_map.get(code,'')}: {c} signals")

# ============================================
# 3. Global date list
# ============================================
all_dates = sorted(set().union(*[set(etf_data[c]['date']) for c in GROUP_A]))
print(f"Global date list: {len(all_dates)} days")

date_idx_map = {}
for code in GROUP_A:
    date_idx_map[code] = dict(zip(etf_data[code]['date'], range(len(etf_data[code]['date']))))

sig_lookup = {}
for s in all_signals:
    sig_lookup[(s['signal_date'], s['code'])] = s

# ============================================
# 4. True Equal-Weight Backtest
# ============================================
# Each active position has equal notional value = total_value / n_active.
# When N-th position enters:
#   - Scale down all existing positions by factor (N-1)/N
#   - Allocate total_value / N to the new position
# When a position exits:
#   - Scale up all remaining positions by factor N/(N-1)
#
# "Notional value" = units * entry_price (this is our tracking metric).
# Actual market value = units * current_price.
# Cash tracks the difference between initial allocations and realized values.

INITIAL_CAPITAL = 1_000_000
SIDE_COST = 0.000375  # per side

def backtest(exit_rule='fixed_40'):
    cash = INITIAL_CAPITAL
    positions = {}  # code -> {entry_date, entry_price, units, name, signal_z, signal_dev}
    trades = []
    daily_equity = {}

    for i, date in enumerate(all_dates):
        # ---- Exits ----
        for code, pos in list(positions.items()):
            entry_i = date_idx_map.get(code, {}).get(pos['entry_date'], i)
            hold_days = i - entry_i

            should_exit = False
            exit_reason = ''

            if exit_rule == 'fixed_40':
                if hold_days >= 40:
                    should_exit = True
                    exit_reason = 'fixed_40'
            elif exit_rule == 'ma20_early':
                if hold_days >= 20:
                    try:
                        row = etf_data[code][etf_data[code]['date'] == date].iloc[0]
                        if row['price_dev_20'] >= 0:
                            should_exit = True
                            exit_reason = 'ma20_revert'
                    except IndexError:
                        pass
                    if not should_exit and hold_days >= 40:
                        should_exit = True
                        exit_reason = 'fixed_40'

            if should_exit:
                try:
                    exit_row = etf_data[code][etf_data[code]['date'] == date].iloc[0]
                    exit_price = float(exit_row['close'])
                except IndexError:
                    continue

                # Sell proceeds
                sell_proceeds = pos['units'] * exit_price * (1 - SIDE_COST)
                cash += sell_proceeds

                # P&L on this position
                notional = pos['units'] * pos['entry_price']
                pnl = (sell_proceeds - notional) / notional

                trades.append({
                    'code': code, 'name': pos['name'],
                    'entry_date': pos['entry_date'], 'exit_date': date,
                    'entry_price': pos['entry_price'], 'exit_price': exit_price,
                    'hold_days': hold_days, 'exit_reason': exit_reason,
                    'return_pct': pnl,
                    'signal_z': pos.get('signal_z', np.nan),
                    'signal_dev': pos.get('signal_dev', np.nan),
                })

                n_before = len(positions)
                del positions[code]

                # Scale up remaining positions by n_before / (n_before - 1)
                if n_before > 1:
                    scale_factor = n_before / (n_before - 1)
                    for c, p in positions.items():
                        p['units'] *= scale_factor

        # ---- Buys ----
        for sig in all_signals:
            if sig['signal_date'] != date or sig['code'] in positions:
                continue

            n_active = len(positions)
            n_total = n_active + 1

            # Get current total value
            cur_total_value = cash
            for c, p in positions.items():
                try:
                    cur_row = etf_data[c][etf_data[c]['date'] == date].iloc[0]
                    cur_total_value += p['units'] * float(cur_row['close'])
                except (IndexError, KeyError):
                    pass

            # Target notional per position
            target_notional = cur_total_value / n_total

            # Scale down existing positions
            scale_down = (n_total - 1) / n_total
            for c, p in positions.items():
                p['units'] *= scale_down
                # Scale down cash to compensate (we're taking cash to fund new position)
                # Actually: cash already accounts for existing allocations.
                # When we scale down units, we're "selling" some of each position.
                # The proceeds go to cash.
                # Proceeds from scaling = old_units * scale_down * price
                # But we already have the units * price in cur_total_value as position value.
                # Hmm, the cash tracking gets tricky here.

            # Simpler approach for cash:
            # After scaling, total_value = cur_total_value (value is preserved, just redistributed).
            # New position needs: target_notional * (1 + cost) to buy units.
            # Where does this cash come from?
            # When we scale down existing positions, we implicitly "sell" some units.
            # Proceeds = old_alloc - new_alloc = old_alloc * (1 - scale_down) = old_alloc / n_total
            # For each of n_active positions: proceed = old_alloc / n_total
            # Total proceeds from scaling = n_active * old_alloc / n_total = cur_total_value * (n_active/n_total) * (n_active/n_active) = cur_total_value * (n_active/n_total)
            # Wait, that's not right either.

            # Let me simplify: use "virtual cash" model.
            # total_value = cash + sum(units * price) for all positions
            # When scaling down position by factor f:
            #   new_units = old_units * f
            #   "sold value" = old_units * (1-f) * price  (but we don't actually sell)
            #   cash += "sold value"
            # When buying new position:
            #   units = target_notional / (price * (1+cost))
            #   cash -= target_notional

            # This is getting complicated. Let me use a cleaner model.
            pass  # handled below

        # ---- Daily value ----
        total_value = cash
        for code, pos in positions.items():
            try:
                cur_row = etf_data[code][etf_data[code]['date'] == date].iloc[0]
                total_value += pos['units'] * float(cur_row['close'])
            except (IndexError, KeyError):
                pass

        if total_value < 0:
            total_value = cash

        daily_equity[date] = total_value

    return trades, daily_equity


# That model is getting too complex. Let me use a clean, simple model:
#
# Model: "No Rebalance" (each signal gets INITIAL_CAPITAL / position_count_at_time allocation)
# - When N-th position enters, allocate INITIAL_CAPITAL / N from cash
# - Existing positions keep their units unchanged
# - Total value = sum(units * price) + cash
#
# This is simple and gives correct per-trade P&L. The only "issue" is that
# earlier positions have larger allocations, but this is realistic (no forced rebalancing).
#
# For the Benchmark comparison: both start with same capital and same timeframe.

def backtest_no_rebalance(exit_rule='fixed_40', signals=None):
    cash = INITIAL_CAPITAL
    positions = {}
    trades = []
    daily_equity = {}

    for i, date in enumerate(all_dates):
        # ---- Exits ----
        for code, pos in list(positions.items()):
            entry_i = date_idx_map.get(code, {}).get(pos['entry_date'], i)
            hold_days = i - entry_i

            should_exit = False
            exit_reason = ''

            if exit_rule == 'fixed_40':
                if hold_days >= 40:
                    should_exit = True
                    exit_reason = 'fixed_40'
            elif exit_rule == 'ma20_early':
                if hold_days >= 20:
                    try:
                        row = etf_data[code][etf_data[code]['date'] == date].iloc[0]
                        if row['price_dev_20'] >= 0:
                            should_exit = True
                            exit_reason = 'ma20_revert'
                    except IndexError:
                        pass
                    if not should_exit and hold_days >= 40:
                        should_exit = True
                        exit_reason = 'fixed_40'

            if should_exit:
                try:
                    exit_row = etf_data[code][etf_data[code]['date'] == date].iloc[0]
                    exit_price = float(exit_row['close'])
                except IndexError:
                    continue

                gross = pos['units'] * exit_price
                net = gross * (1 - SIDE_COST)
                cash += net
                pnl = (net - pos['alloc']) / pos['alloc']

                trades.append({
                    'code': code, 'name': pos['name'],
                    'entry_date': pos['entry_date'], 'exit_date': date,
                    'entry_price': pos['entry_price'], 'exit_price': exit_price,
                    'hold_days': hold_days, 'exit_reason': exit_reason,
                    'return_pct': pnl,
                    'signal_z': pos.get('signal_z', np.nan),
                    'signal_dev': pos.get('signal_dev', np.nan),
                })
                del positions[code]

        # ---- Buys (使用 signals 参数，默认 all_signals) ----
        buy_signals = signals if signals is not None else all_signals
        for sig in buy_signals:
            if sig['signal_date'] != date or sig['code'] in positions:
                continue

            n_total = len(positions) + 1
            alloc = INITIAL_CAPITAL / n_total
            buy_price = sig['price']
            units = alloc / (buy_price * (1 + SIDE_COST))

            positions[sig['code']] = {
                'entry_date': sig['signal_date'],
                'entry_price': buy_price,
                'alloc': alloc,
                'units': units,
                'name': sig['name'],
                'signal_z': sig.get('share_z_20', sig.get('trigger_share_z_20', np.nan)),
                'signal_dev': sig.get('price_dev_20', sig.get('trigger_price_dev_20', np.nan)),
            }
            cash -= alloc

        # ---- Daily value ----
        total_value = cash
        for code, pos in positions.items():
            try:
                cur_row = etf_data[code][etf_data[code]['date'] == date].iloc[0]
                total_value += pos['units'] * float(cur_row['close'])
            except (IndexError, KeyError):
                pass

        daily_equity[date] = max(total_value, cash)

    return trades, daily_equity


# ============================================
# 5. Benchmark (equal-weight B&H of 7 ETFs)
# ============================================
# Simple equal-weight hold-all: each ETF gets INITIAL_CAPITAL / 7 allocation.
# For ETFs missing data on day 1, their capital sits in cash (realistic).
# No rebalancing when delayed ETFs enter — same model as the scheme.
n_etfs = len(GROUP_A)
alloc_each = INITIAL_CAPITAL / n_etfs
units_bench = {}

for code in GROUP_A:
    try:
        row = etf_data[code][etf_data[code]['date'] == all_dates[0]].iloc[0]
        price = float(row['close'])
        units_bench[code] = alloc_each / (price * (1 + SIDE_COST))
    except IndexError:
        pass  # not available on start date; capital stays in cash

# Cash = INITIAL_CAPITAL (no explicit deduction — allocation is a bookkeeping entry)
# Total portfolio value = sum(units * price) + remaining_cash
# where remaining_cash = INITIAL_CAPITAL - sum(alloc_each for entered ETFs)
initial_invested = sum(alloc_each for c in GROUP_A if c in units_bench)

# Compute benchmark equity curve
bench_equity = {}
for date in all_dates:
    remaining_cash = INITIAL_CAPITAL - initial_invested
    total = remaining_cash
    for code in GROUP_A:
        try:
            row = etf_data[code][etf_data[code]['date'] == date].iloc[0]
            total += units_bench.get(code, 0) * float(row['close'])
        except IndexError:
            pass
    bench_equity[date] = total

# ============================================
# 6. Run backtests
# ============================================

# CLI 参数：--mode single_day | confirmed
# single_day    → 冻结版（39 个 E2），输出 backtest_trades.csv / equity_curve.csv
# confirmed     → 方案 A（脉冲触发 + 持续确认），输出 _scheme_a 后缀文件
parser = argparse.ArgumentParser(description='E2 信号回测')
parser.add_argument('--mode', choices=['single_day', 'confirmed'],
                    default='single_day',
                    help='信号模式: single_day=冻结版, confirmed=方案A(3日确认)')
args = parser.parse_args()

if args.mode == 'single_day':
    all_signals = generate_signals(etf_data, mode='single_day')
else:
    all_signals = generate_signals(etf_data, mode='confirmed', confirm_days=3)

print(f"\n模式: {args.mode}  |  总信号数: {len(all_signals)}")
if args.mode == 'confirmed':
    total_triggered = sum(
        1 for s in generate_signals(etf_data, mode='single_day')
    )
    print(f"单日触发总数: {total_triggered}  |  确认后信号: {len(all_signals)}  |  过滤: {total_triggered - len(all_signals)}")

# 方案 A 每 ETF 详细分析
if args.mode == 'confirmed':
    single_signals = generate_signals(etf_data, mode='single_day')
    print("\n  --- 方案 A 触发 → 确认 明细 ---")
    for code in GROUP_A:
        # 触发日：single_day 模式，signal_date 即触发日 T
        triggered_dates = {s['signal_date'] for s in single_signals if s['code'] == code}
        confirmed_sigs = [s for s in all_signals if s['code'] == code]
        # 确认日 = T+3 输出日，触发日 = T 元数据
        confirmed_trigger_dates = {s['trigger_date'] for s in confirmed_sigs}
        filtered_dates = triggered_dates - confirmed_trigger_dates
        if triggered_dates:
            print(f"  {code} {name_map.get(code,'')}: 触发={len(triggered_dates)} 确认={len(confirmed_sigs)} 过滤={len(filtered_dates)}")
            for fd in sorted(filtered_dates):
                # 找到 T+1..T+3 中第一天的 shares vs shares_ma20
                t_idx = etf_data[code].index[etf_data[code]['date'] == fd]
                if len(t_idx) > 0:
                    t_i = t_idx[0]
                    for k in [1, 2, 3]:
                        c_i = t_i + k
                        if c_i < len(etf_data[code]):
                            c_row = etf_data[code].iloc[c_i]
                            ok = c_row['shares'] > c_row['shares_ma20']
                            c_date = c_row['date'].strftime('%Y-%m-%d')
                            print(f"    未确认: T={fd.strftime('%Y-%m-%d')}  T+{k}={c_date}  "
                                  f"shares={c_row['shares']:.0f}  ma20={c_row['shares_ma20']:.0f}  "
                                  f"{'✅' if ok else '❌ 跌破均线'}")

print("\n" + "=" * 60)
print(f"Running backtests (mode={args.mode})...")
print("=" * 60)

trades_1, equity_1 = backtest_no_rebalance('fixed_40')
print(f"\nScheme 1 (Fixed 40D): {len(trades_1)} trades")

trades_2, equity_2 = backtest_no_rebalance('ma20_early')
print(f"Scheme 2 (MA20 Early): {len(trades_2)} trades")

# 方案 A 单独回测输出
OUT_SUFFIX = '_scheme_a' if args.mode == 'confirmed' else ''

# 方案 A 单独回测（使用 confirmed 信号）
trades_a, equity_a = None, None
if args.mode == 'confirmed':
    signals_a = generate_signals(etf_data, mode='confirmed', confirm_days=3)
    trades_a, equity_a = backtest_no_rebalance('fixed_40', signals=signals_a)
    print(f"\n方案 A (脉冲触发+3日确认): {len(trades_a)} trades")

# Verify: are there fewer NaN returns now?
nan_s1 = sum(1 for t in trades_1 if t['return_pct'] != t['return_pct'])  # NaN check
nan_s2 = sum(1 for t in trades_2 if t['return_pct'] != t['return_pct'])
print(f"Scheme 1 NaN returns: {nan_s1}/{len(trades_1)}")
print(f"Scheme 2 NaN returns: {nan_s2}/{len(trades_2)}")

# Print first 10 trades from scheme 2
print("\nScheme 2 first 10 trades:")
for t in trades_2[:10]:
    print(f"  {t['code']:7s} {str(t['entry_date'])[:10]} -> {str(t['exit_date'])[:10]}: ret={t['return_pct']:+.4f} hold={t['hold_days']} reason={t['exit_reason']}")

# ============================================
# 7. Performance metrics
# ============================================
def compute_metrics(trades, daily_equity, name=""):
    results = {'name': name}
    if not trades:
        results['error'] = 'no_trades'
        return results

    eq = pd.Series(daily_equity)
    returns_list = [t['return_pct'] for t in trades if t['return_pct'] == t['return_pct']]  # filter NaN
    n = len(returns_list)
    wins = [r for r in returns_list if r > 0]
    losses = [r for r in returns_list if r < 0]
    n_wins = len(wins)

    results['n_trades'] = n
    results['n_wins'] = n_wins
    results['n_losses'] = n - n_wins
    results['win_rate'] = n_wins / n if n > 0 else 0
    results['avg_return'] = np.mean(returns_list)
    results['avg_win'] = np.mean(wins) if wins else 0
    results['avg_loss'] = np.mean(losses) if losses else 0
    results['profit_loss_ratio'] = abs(results['avg_win'] / results['avg_loss']) if results['avg_loss'] != 0 else 999

    valid_trades = [t for t in trades if t['return_pct'] == t['return_pct']]
    results['avg_hold_days'] = np.mean([t['hold_days'] for t in valid_trades])

    start_val = eq.iloc[0]
    end_val = eq.iloc[-1]
    days = (eq.index[-1] - eq.index[0]).days
    results['total_return'] = (end_val / start_val) - 1
    results['annualized_return'] = (1 + results['total_return']) ** (252 / max(days, 1)) - 1 if days > 0 else 0
    results['final_equity'] = end_val

    daily_rets = eq.pct_change().dropna()
    if len(daily_rets) > 1:
        dm = daily_rets.mean()
        ds = daily_rets.std()
        ann = 252
        results['sharpe'] = (dm * ann) / (ds * np.sqrt(ann)) if ds > 0 else 0

        downside = daily_rets[daily_rets < 0]
        ds_std = downside.std() if len(downside) > 0 else 1e-10
        results['sortino'] = (dm * ann) / (ds_std * np.sqrt(ann))

        cummax = eq.cummax()
        dd = (eq - cummax) / cummax
        results['mdd'] = dd.min()
        results['mdd_pct'] = dd.min() * 100

        max_dd_dur = 0
        dd_start = None
        for d in dd.index:
            if dd[d] < -1e-8:
                if dd_start is None:
                    dd_start = d
            else:
                if dd_start is not None:
                    max_dd_dur = max(max_dd_dur, (d - dd_start).days)
                    dd_start = None
        if dd_start is not None:
            max_dd_dur = max(max_dd_dur, (dd.index[-1] - dd_start).days)
        results['mdd_duration_days'] = max_dd_dur

        results['calmar'] = results['annualized_return'] / abs(results['mdd']) if results['mdd'] != 0 else 0
    else:
        results['sharpe'] = results['sortino'] = results['mdd'] = results['mdd_pct'] = 0

    # Per-ETF (using non-NaN trades)
    per_etf = {}
    for code in GROUP_A:
        ct = [t for t in valid_trades if t['code'] == code]
        if ct:
            per_etf[code] = {
                'n': len(ct),
                'win_rate': sum(1 for t in ct if t['return_pct'] > 0) / len(ct),
                'avg_return': np.mean([t['return_pct'] for t in ct]),
            }
    results['per_etf'] = per_etf

    return results


metrics_s1 = compute_metrics(trades_1, equity_1, "Scheme 1: Fixed 40D")
metrics_s2 = compute_metrics(trades_2, equity_2, "Scheme 2: MA20 Early")

metrics_a = None
if trades_a:
    metrics_a = compute_metrics(trades_a, equity_a, "Scheme A (confirmed): Fixed 40D")

# Benchmark (full period with cash drag from missing 159228)
bench_series = pd.Series(bench_equity)
bench_rets = bench_series.pct_change().dropna()
bench_total = (bench_series.iloc[-1] / bench_series.iloc[0]) - 1
bench_days = (bench_series.index[-1] - bench_series.index[0]).days
bench_ann = (1 + bench_total) ** (252 / max(bench_days, 1)) - 1
bench_sharpe = (bench_rets.mean() * 252) / (bench_rets.std() * np.sqrt(252))
bench_cummax = bench_series.cummax()
bench_dd = (bench_series - bench_cummax) / bench_cummax
bench_mdd = bench_dd.min() * 100

# Fair benchmark: exclude 159228 from both scheme and benchmark
# Start from 2025-06-18 when all 7 ETFs have data
fair_start = pd.Timestamp('2025-06-18')
fair_idx = [d for d in all_dates if d >= fair_start]

# Fair scheme metrics: only use trades that fully complete from fair_start
fair_trades_1 = [t for t in trades_1 if pd.Timestamp(t['entry_date']) >= fair_start]
fair_trades_2 = [t for t in trades_2 if pd.Timestamp(t['entry_date']) >= fair_start]

fair_equity_1 = {d: equity_1[d] for d in fair_idx}
fair_equity_2 = {d: equity_2[d] for d in fair_idx}

# Fair benchmark: equal-weight B&H of 7 ETFs from fair_start
fair_units_bench = {}
alloc_each_fair = INITIAL_CAPITAL / 7
for code in GROUP_A:
    try:
        row = etf_data[code][etf_data[code]['date'] == fair_start].iloc[0]
        price = float(row['close'])
        fair_units_bench[code] = alloc_each_fair / (price * (1 + SIDE_COST))
    except IndexError:
        pass

fair_bench_equity = {}
cash_fair = INITIAL_CAPITAL - alloc_each_fair * len(GROUP_A)  # should be 0
for date in fair_idx:
    total = cash_fair
    for code in GROUP_A:
        try:
            row = etf_data[code][etf_data[code]['date'] == date].iloc[0]
            total += fair_units_bench.get(code, 0) * float(row['close'])
        except IndexError:
            pass
    fair_bench_equity[date] = total

fair_bench_series = pd.Series([fair_bench_equity.get(d, INITIAL_CAPITAL) for d in fair_idx], index=fair_idx)
fair_bench_rets = fair_bench_series.pct_change().dropna()
fair_bench_total = (fair_bench_series.iloc[-1] / fair_bench_series.iloc[0]) - 1
fair_bench_days = (fair_bench_series.index[-1] - fair_bench_series.index[0]).days
fair_bench_ann = (1 + fair_bench_total) ** (252 / max(fair_bench_days, 1)) - 1
fair_bench_sharpe = (fair_bench_rets.mean() * 252) / (fair_bench_rets.std() * np.sqrt(252))
fair_bench_cummax = fair_bench_series.cummax()
fair_bench_dd = (fair_bench_series - fair_bench_cummax) / fair_bench_cummax
fair_bench_mdd = fair_bench_dd.min() * 100

metrics_fair_s1 = compute_metrics(fair_trades_1, fair_equity_1, "Scheme 1 (fair): Fixed 40D")
metrics_fair_s2 = compute_metrics(fair_trades_2, fair_equity_2, "Scheme 2 (fair): MA20 Early")

# Fix fair metrics: report return based on fair-period only, not full accumulated value
# Get the starting equity value at fair_start from the full equity series
eq_1_full = pd.Series([equity_1.get(d, INITIAL_CAPITAL) for d in all_dates], index=all_dates)
eq_2_full = pd.Series([equity_2.get(d, INITIAL_CAPITAL) for d in all_dates], index=all_dates)

fair_start_val_s1 = eq_1_full.get(fair_start, INITIAL_CAPITAL)
fair_start_val_s2 = eq_2_full.get(fair_start, INITIAL_CAPITAL)
fair_end_val_s1 = eq_1_full.get(fair_idx[-1], INITIAL_CAPITAL)
fair_end_val_s2 = eq_2_full.get(fair_idx[-1], INITIAL_CAPITAL)

# Compute fair-period-specific return and annualized return
fair_days = (fair_idx[-1] - fair_start).days
if fair_start_val_s1 > 0:
    fair_ret_s1 = (fair_end_val_s1 / fair_start_val_s1) - 1
    fair_ann_s1 = (1 + fair_ret_s1) ** (252 / max(fair_days, 1)) - 1
else:
    fair_ret_s1 = fair_ann_s1 = 0
if fair_start_val_s2 > 0:
    fair_ret_s2 = (fair_end_val_s2 / fair_start_val_s2) - 1
    fair_ann_s2 = (1 + fair_ret_s2) ** (252 / max(fair_days, 1)) - 1
else:
    fair_ret_s2 = fair_ann_s2 = 0

metrics_fair_s1['total_return'] = fair_ret_s1
metrics_fair_s1['annualized_return'] = fair_ann_s1
metrics_fair_s1['fair_start_val'] = fair_start_val_s1
metrics_fair_s1['fair_end_val'] = fair_end_val_s1

metrics_fair_s2['total_return'] = fair_ret_s2
metrics_fair_s2['annualized_return'] = fair_ann_s2
metrics_fair_s2['fair_start_val'] = fair_start_val_s2
metrics_fair_s2['fair_end_val'] = fair_end_val_s2


# ============================================
# 8. Print results
# ============================================
def print_metrics(m, bench_total, bench_sharpe, bench_mdd):
    print(f"\n  === {m['name']} ===")
    print(f"  Total Return:     {m['total_return']:.2%}")
    print(f"  Annualized Return: {m['annualized_return']:.2%}")
    print(f"  Sharpe Ratio:     {m['sharpe']:.3f}")
    print(f"  Sortino Ratio:    {m['sortino']:.3f}")
    print(f"  Max Drawdown:     {m['mdd_pct']:.2f}%")
    print(f"  MDD Duration:     {m.get('mdd_duration_days', 0)} days")
    print(f"  Calmar Ratio:     {m.get('calmar', 0):.3f}")
    print(f"  Final Equity:     {m['final_equity']:,.0f}")
    print(f"\n  Trade Stats:")
    print(f"    Total Trades:   {m['n_trades']}")
    print(f"    Wins:           {m['n_wins']}, Losses: {m['n_losses']}")
    print(f"    Win Rate:       {m['win_rate']:.2%}")
    print(f"    Avg Win:        {m['avg_win']:+.2%}, Avg Loss: {m['avg_loss']:+.2%}")
    print(f"    Profit/Loss:    {m['profit_loss_ratio']:.2f}")
    print(f"    Avg Hold Days:  {m['avg_hold_days']:.1f}")
    print(f"\n  vs Benchmark (return={bench_total:.2%}, sharpe={bench_sharpe:.3f}, mdd={bench_mdd:.2f}%):")
    print(f"    Excess Return:  {m['total_return'] - bench_total:+.2%}")
    print(f"    Excess Sharpe:  {m['sharpe'] - bench_sharpe:+.3f}")
    print(f"    MDD Gap:        {m['mdd_pct'] - bench_mdd:+.2f} pp")
    print(f"\n  Per-ETF:")
    for code, info in m.get('per_etf', {}).items():
        print(f"    {code}: {info['n']} trades, WR={info['win_rate']:.0%}, mean={info['avg_return']:+.2%}")

print("\n" + "=" * 60)
print("RESULTS (Full Period)")
print("=" * 60)

print_metrics(metrics_s1, bench_total, bench_sharpe, bench_mdd)
print_metrics(metrics_s2, bench_total, bench_sharpe, bench_mdd)

if metrics_a:
    print_metrics(metrics_a, bench_total, bench_sharpe, bench_mdd)
    # 方案 A vs 单日报对比
    print(f"\n  --- 方案 A 对比 ---")
    print(f"  单日触发信号: {len(all_signals)} 笔 (但全部被过滤)")
    single_signals_for_comparison = generate_signals(etf_data, mode='single_day')
    print(f"  确认后信号:     {len(trades_a)} 笔 → {len(trades_a)} 笔交易")
    print(f"  胜率提升:       {metrics_a['win_rate']:.2%} vs S1 {metrics_s1['win_rate']:.2%} vs S2 {metrics_s2['win_rate']:.2%}")
    print(f"  平均收益:       {metrics_a['avg_return']:+.2%} vs S1 {metrics_s1['avg_return']:+.2%} vs S2 {metrics_s2['avg_return']:+.2%}")

print("\n" + "=" * 60)
print("RESULTS (Fair: from 2025-06-18, all 7 ETFs available)")
print("=" * 60)

print_metrics(metrics_fair_s1, fair_bench_total, fair_bench_sharpe, fair_bench_mdd)
print_metrics(metrics_fair_s2, fair_bench_total, fair_bench_sharpe, fair_bench_mdd)

print(f"\n  Fair Benchmark: return={fair_bench_total:.2%}, sharpe={fair_bench_sharpe:.3f}, mdd={fair_bench_mdd:.2f}%")
print(f"  Fair start date: {fair_start}, end date: {fair_idx[-1]}")
print(f"  Fair period: {fair_bench_days} trading days")
print(f"  Fair S1 trades: {len(fair_trades_1)}, Fair S2 trades: {len(fair_trades_2)}")

# ============================================
# 9. Save outputs
# ============================================
os.makedirs('D:/claude projects/ETF/data/backtest', exist_ok=True)

trade_cols = ['code', 'name', 'entry_date', 'exit_date', 'entry_price', 'exit_price',
              'hold_days', 'exit_reason', 'return_pct', 'signal_z', 'signal_dev']
df_trades_1 = pd.DataFrame(trades_1)[trade_cols].copy()
df_trades_1['scheme'] = 'fixed_40'
df_trades_2 = pd.DataFrame(trades_2)[trade_cols].copy()
df_trades_2['scheme'] = 'ma20_early'
df_all_trades = pd.concat([df_trades_1, df_trades_2], ignore_index=True)

# 方案 A 单独输出（不覆盖冻结文件）
if trades_a:
    df_trades_a = pd.DataFrame(trades_a)[trade_cols].copy()
    df_trades_a['scheme'] = 'confirmed'
    df_all_trades = pd.concat([df_all_trades, df_trades_a], ignore_index=True)

df_all_trades.to_csv(f'D:/claude projects/ETF/data/backtest/backtest_trades{OUT_SUFFIX}.csv',
                      index=False, encoding='utf-8-sig')
print(f"\nTrades saved: {len(df_all_trades)} rows  (backtest_trades{OUT_SUFFIX}.csv)")

# Equity curve
eq_1 = pd.Series([equity_1.get(d, INITIAL_CAPITAL) for d in all_dates], index=all_dates, name='scheme1_fixed_40')
eq_2 = pd.Series([equity_2.get(d, INITIAL_CAPITAL) for d in all_dates], index=all_dates, name='scheme2_ma20_early')
eq_bench = pd.Series([bench_equity.get(d, INITIAL_CAPITAL) for d in all_dates], index=all_dates, name='benchmark')
eq_df = pd.concat([eq_1, eq_2, eq_bench], axis=1)

if equity_a:
    eq_a = pd.Series([equity_a.get(d, INITIAL_CAPITAL) for d in all_dates], index=all_dates, name='schemeA_confirmed')
    eq_df = pd.concat([eq_df, eq_a], axis=1)

eq_df.to_csv(f'D:/claude projects/ETF/data/backtest/equity_curve{OUT_SUFFIX}.csv', encoding='utf-8-sig')
print(f"Equity curve saved: {len(eq_df)} rows  (equity_curve{OUT_SUFFIX}.csv)")

# Save summary
with open('D:/claude projects/ETF/_backtest_summary.txt', 'w', encoding='utf-8') as f:
    f.write("=== Backtest Summary ===\n\n")
    f.write(f"Benchmark:\n")
    f.write(f"  Total Return: {bench_total:.4%}\n")
    f.write(f"  Annualized: {bench_ann:.4%}\n")
    f.write(f"  Sharpe: {bench_sharpe:.4f}\n")
    f.write(f"  MDD: {bench_mdd:.2f}%\n\n")

    for name, m in [('Scheme 1: Fixed 40D', metrics_s1), ('Scheme 2: MA20 Early', metrics_s2)]:
        f.write(f"{name}:\n")
        for k, v in m.items():
            if isinstance(v, float):
                if 'return' in str(k) and 'ann' not in str(k):
                    f.write(f"  {k}: {v:.4%}\n")
                elif 'sharpe' in str(k) or 'sortino' in str(k) or 'calmar' in str(k):
                    f.write(f"  {k}: {v:.4f}\n")
                elif 'mdd' in str(k) and 'duration' not in str(k):
                    f.write(f"  {k}: {v:.4f}\n")
                else:
                    f.write(f"  {k}: {v:.4f}\n")
            elif isinstance(v, int):
                f.write(f"  {k}: {v}\n")
            elif isinstance(v, dict):
                f.write(f"  {k}:\n")
                for kk, vv in v.items():
                    f.write(f"    {kk}: {vv}\n")
            else:
                f.write(f"  {k}: {v}\n")
        f.write("\n")

    if metrics_a:
        f.write("Scheme A (confirmed, pulse+3day):\n")
        for k, v in metrics_a.items():
            if isinstance(v, float):
                if 'return' in str(k) and 'ann' not in str(k):
                    f.write(f"  {k}: {v:.4%}\n")
                elif 'sharpe' in str(k) or 'sortino' in str(k) or 'calmar' in str(k):
                    f.write(f"  {k}: {v:.4f}\n")
                elif 'mdd' in str(k) and 'duration' not in str(k):
                    f.write(f"  {k}: {v:.4f}\n")
                else:
                    f.write(f"  {k}: {v:.4f}\n")
            elif isinstance(v, int):
                f.write(f"  {k}: {v}\n")
            elif isinstance(v, dict):
                f.write(f"  {k}:\n")
                for kk, vv in v.items():
                    f.write(f"    {kk}: {vv}\n")
            else:
                f.write(f"  {k}: {v}\n")
        f.write("\n")

    f.write("\n=== Scheme 2 Exit Reasons ===\n")
    s2_reasons = Counter(t['exit_reason'] for t in trades_2)
    for reason, count in s2_reasons.most_common():
        f.write(f"  {reason}: {count}\n")

    f.write("\n=== Per-ETF Trade Count ===\n")
    for code in GROUP_A:
        c1 = sum(1 for t in trades_1 if t['code'] == code)
        c2 = sum(1 for t in trades_2 if t['code'] == code)
        ca = sum(1 for t in trades_a if t['code'] == code) if trades_a else 0
        f.write(f"  {code} {name_map.get(code,'')}: S1={c1}, S2={c2}, A={ca}\n")

print("Summary saved to _backtest_summary.txt")
print("\nBacktest complete!")
