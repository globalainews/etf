# -*- coding: utf-8 -*-
"""Quick test: run only benchmark experiment"""
import sys
import io
import warnings
warnings.filterwarnings('ignore')

from src.strategy.step4_optimization import run_backtest_v2, evaluate_performance

params = {
    'rebalance_mode': 'daily',
    'score_buffer': 0.0,
    'min_score_threshold': None,
    'linear_weight': False,
    'use_ma60_filter': False,
}
print('Starting benchmark...')
result = run_backtest_v2(start_date='2019-04-01', params=params)
perf = evaluate_performance(result)
print(f"CAGR={perf['strategy_cagr']:+.2%}  MaxDD={perf['max_dd']:+.2%}  Sharpe={perf['sharpe']:.2f}  换手={perf['total_turnover']:.0%}  成本={perf['total_cost']:+.2%}")
print(f"Trades: {len(result['trades'])}")
print(f"Cash NAV: {result['cash_nav'].iloc[-1]:.4f}")
print(f"Bench NAV: {result['benchmark_nav'].iloc[-1]:.4f}")
print("DONE")
