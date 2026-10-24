# -*- coding: utf-8 -*-
"""Debug: trace T+1 signal lookup"""
import sys
sys.path.insert(0, '/d/claude projects/ETF/src')

import warnings
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
import sqlite3
from src.strategy.signal_generator import generate_signals
from src.strategy.multi_factor_builder import DB_PATH

# Load signals
sig = generate_signals(start_date='2019-04-01')
all_dates = sorted(sig['date'].unique())

# Build sig_dict (same as backtest)
sig_dict = {}
for d, day in sig.groupby('date'):
    entries = []
    for _, row in day.iterrows():
        if row['weight'] > 0.01:
            entries.append((row['etf_code'], row['weight']))
    if entries:
        sig_dict[d] = entries

# Load price
conn = sqlite3.connect(DB_PATH)
price_all = pd.read_sql("SELECT date, code, close FROM fund_price", conn)
price_all['date'] = pd.to_datetime(price_all['date'])
conn.close()
price_map = {}
for d, day in price_all.groupby('date'):
    price_map[d] = dict(zip(day['code'], day['close']))

# Simulate T+1 backtest (daily mode)
warmup = 120
current_hold = {}
trades = []
last_trade_idx = None

for i, date in enumerate(all_dates):
    if i == 0:
        continue
    prev_date = all_dates[i - 1]

    # Warmup
    if i <= warmup:
        continue

    # daily mode: trade every day
    should_trade = True

    if not should_trade:
        continue

    # Get signal for prev_date (T+1)
    entries = sig_dict.get(prev_date, [])
    print(f"i={i}, date={date.date()}, prev_date={prev_date.date()}, entries={len(entries)}")

    if not entries:
        turnover = sum(current_hold.values())
        if turnover > 0:
            trades.append({'date': date, 'turnover': turnover})
        current_hold = {}
        last_trade_idx = i
        continue

    target_weights = dict(entries)
    prev_hold = dict(current_hold)

    # Turnover
    turnover = 0.0
    for code, old_w in prev_hold.items():
        new_w = target_weights.get(code, 0)
        if old_w - new_w > 0.01:
            turnover += old_w - new_w
    for code, new_w in target_weights.items():
        old_w = prev_hold.get(code, 0)
        if new_w - old_w > 0.01:
            turnover += new_w - old_w

    # Truncate
    max_turnover = 0.5
    if turnover > max_turnover:
        scale = max_turnover / turnover
        for code, new_w in list(target_weights.items()):
            old_w = prev_hold.get(code, 0)
            delta = new_w - old_w
            if delta > 0:
                target_weights[code] = old_w + delta * scale
            elif delta < 0:
                target_weights[code] = old_w - abs(delta) * scale
        total_w = sum(target_weights.values())
        if total_w > 0 and abs(total_w - 1.0) > 0.01:
            for code in target_weights:
                target_weights[code] *= (1.0 / total_w)
        turnover = max_turnover

    current_hold = {c: w for c, w in target_weights.items() if w > 0.01}

    if turnover > 0:
        trades.append({
            'date': date,
            'turnover': round(turnover, 4),
            'target_weights': {k: round(v, 3) for k, v in target_weights.items()},
        })

    last_trade_idx = i

print(f"\nTotal trades: {len(trades)}")
total_turnover = sum(t['turnover'] for t in trades)
print(f"Total turnover: {total_turnover:.0%}")

# Show first 20 trades
for t in trades[:20]:
    print(f"  {t['date'].date()}: turnover={t['turnover']:.2%}, codes={t['target_weights']}")
