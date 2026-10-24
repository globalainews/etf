# -*- coding: utf-8 -*-
"""单元测试：方案 A 向量化信号引擎 vs 朴素参考实现 + 真实数据核对"""
import unittest
import numpy as np
import pandas as pd
import sqlite3

# ---- 复用 _backtest_e2.py 的 generate_signals / make_e2_signal_mask ----
import sys, importlib.util
spec = importlib.util.spec_from_file_location(
    'backtest_e2', 'D:/claude projects/ETF/_backtest_e2.py')
mod = importlib.util.module_from_spec(spec)
# 阻止模块执行 main 逻辑：monkey-patch argparse
import argparse as _ap
_orig_parse = _ap.ArgumentParser.parse_args
def _noop_parse(self, *a, **kw):
    class O:
        mode = 'single_day'
    return O()
_ap.ArgumentParser.parse_args = _noop_parse
spec.loader.exec_module(mod)
# 恢复
_ap.ArgumentParser.parse_args = _orig_parse

generate_signals = mod.generate_signals
make_e2_signal_mask = mod.make_e2_signal_mask
GROUP_A = mod.GROUP_A
name_map = mod.name_map
etf_data = mod.etf_data

conn = sqlite3.connect('D:/claude projects/ETF/etf.db')
scale_df = pd.read_sql_query(
    "SELECT date, code, shares FROM fund_scale WHERE code IN ({})".format(
        ','.join(f'"{c}"' for c in GROUP_A)), conn)
scale_df['date'] = pd.to_datetime(scale_df['date'])
conn.close()


# ===================================================================
#  辅助函数
# ===================================================================

def _build_etf_data_fake(shares_list, close_list, dates_list):
    """构造最小化 etf_data 字典用于单元测试"""
    return {
        'FAKE': pd.DataFrame({
            'date': pd.to_datetime(dates_list),
            'close': close_list,
            'shares': shares_list,
            'ret_1d': np.nan,
            'ma20': np.nan,
            'price_dev_20': np.nan,
            'share_chg_1d': np.nan,
            'w20m': np.nan,
            'w20s': np.nan,
            'share_z_20': np.nan,
            'shares_ma20': np.nan,
            'name': 'FAKE',
        }).sort_values('date').reset_index(drop=True),
    }


def _compute_indicators(df):
    """为 fake df 补齐 indicators（ma20, share_z_20, shares_ma20 等）"""
    df = df.copy()
    df['ret_1d'] = df['close'].pct_change(1)
    df['ma20'] = df['close'].rolling(20, min_periods=20).mean()
    df['price_dev_20'] = df['close'] / df['ma20'] - 1
    df['share_chg_1d'] = df['shares'].pct_change(1)
    w20m = df['share_chg_1d'].rolling(20, min_periods=20).mean()
    w20s = df['share_chg_1d'].rolling(20, min_periods=20).std()
    df['share_z_20'] = (df['share_chg_1d'] - w20m) / w20s.replace(0, np.nan)
    df['shares_ma20'] = df['shares'].rolling(20, min_periods=20).mean()
    return df


def _naive_generate_signals(etf_data_dict, mode='single_day', confirm_days=3):
    """朴素参考实现（for-loop，用于验证向量化版本）"""
    all_sig = []
    for code, df in etf_data_dict.items():
        if mode == 'single_day':
            trigger_mask = make_e2_signal_mask(df)
            for idx in df.index[trigger_mask]:
                r = df.loc[idx]
                all_sig.append({
                    'code': code, 'signal_date': r['date'],
                    'price': float(r['close']),
                    'share_z_20': float(r['share_z_20']),
                    'price_dev_20': float(r['price_dev_20']),
                })
        else:
            trigger_mask = make_e2_signal_mask(df)
            ok = (df['shares'] > df['shares_ma20']).fillna(False)
            confirm_mask = (ok.rolling(confirm_days, min_periods=confirm_days)
                              .sum() == confirm_days)
            signal_mask = trigger_mask.shift(confirm_days) & confirm_mask
            for idx in df.index[signal_mask]:
                t_idx = idx - confirm_days
                trig = df.loc[t_idx]
                all_sig.append({
                    'code': code,
                    'signal_date': df['date'].iloc[idx],
                    'trigger_date': trig['date'],
                    'price': float(df['close'].iloc[idx]),
                    'trigger_share_z_20': float(trig['share_z_20']),
                    'trigger_price_dev_20': float(trig['price_dev_20']),
                })
    all_sig.sort(key=lambda x: str(x['signal_date']))
    return all_sig


class TestGenerateSignals(unittest.TestCase):
    """向量化 vs 朴素实现等价性测试"""

    def test_single_day_vectorized_equals_naive(self):
        """方案：向量化 single_day == 朴素 for-loop"""
        # 复用 etf_data（已包含 GROUP_A 的 indicators）
        sig_vec = generate_signals(etf_data, mode='single_day')
        sig_naive = _naive_generate_signals(etf_data, mode='single_day')
        self.assertEqual(len(sig_vec), len(sig_naive),
                         f"向量化 {len(sig_vec)} != 朴素 {len(sig_naive)}")
        for v, n in zip(sig_vec, sig_naive):
            self.assertEqual(v['code'], n['code'])
            self.assertEqual(str(v['signal_date'])[:10], str(n['signal_date'])[:10])
            self.assertAlmostEqual(v['price'], n['price'], places=6)

    def test_confirmed_vectorized_equals_naive(self):
        """方案 A：向量化 confirmed == 朴素 for-loop"""
        sig_vec = generate_signals(etf_data, mode='confirmed', confirm_days=3)
        sig_naive = _naive_generate_signals(etf_data, mode='confirmed', confirm_days=3)
        self.assertEqual(len(sig_vec), len(sig_naive),
                         f"向量化 {len(sig_vec)} != 朴素 {len(sig_naive)}")
        for v, n in zip(sig_vec, sig_naive):
            self.assertEqual(v['code'], n['code'])
            self.assertEqual(str(v['signal_date'])[:10], str(n['signal_date'])[:10])
            self.assertAlmostEqual(v['price'], n['price'], places=6)

    def test_no_false_breakout_in_synthetic(self):
        """合成数据：T 触发但 T+1 份额跌破均线 → 应被过滤"""
        # 构造 30 天数据
        # 前 25 天：shares 稳定在 100，close 稳定在 10
        # 第 26 天 (T): shares 暴涨到 200, close 跌到 9 → 触发
        # 第 27 天 (T+1): shares 跌回 90 (跌破 ma20~100) → 应被过滤
        # 第 28~30 天: shares 100, close 10
        dates = [pd.Timestamp('2025-01-01') + pd.Timedelta(days=i) for i in range(30)]
        closes = [10.0] * 30
        closes[25] = 9.0  # T: price drop
        shares = [100.0] * 30
        shares[25] = 200.0  # T: share spike

        fake = _build_etf_data_fake(shares, closes, dates)
        fake['FAKE'] = _compute_indicators(fake['FAKE'])

        single = generate_signals(fake, mode='single_day')
        confirmed = generate_signals(fake, mode='confirmed', confirm_days=3)

        self.assertEqual(len(single), 1, "单日报应检测到 1 个触发")
        self.assertEqual(len(confirmed), 0,
                         "方案 A 应过滤：T+1 shares=90 < ma20~100，假突破")

    def test_no_false_breakout_on_day2(self):
        """合成数据：T 触发，T+1 OK，T+2 跌破均线 → 应被过滤"""
        dates = [pd.Timestamp('2025-01-01') + pd.Timedelta(days=i) for i in range(30)]
        closes = [10.0] * 30
        closes[25] = 9.0
        shares = [100.0] * 30
        shares[25] = 200.0  # T: spike
        shares[26] = 105.0  # T+1: OK (above ma20 ~100)
        shares[27] = 90.0   # T+2: drop below ma20 → 应被过滤

        fake = _build_etf_data_fake(shares, closes, dates)
        fake['FAKE'] = _compute_indicators(fake['FAKE'])

        single = generate_signals(fake, mode='single_day')
        confirmed = generate_signals(fake, mode='confirmed', confirm_days=3)

        self.assertEqual(len(single), 1)
        self.assertEqual(len(confirmed), 0,
                         "方案 A 应过滤：T+2 shares=90 < ma20，假突破")

    def test_all_three_days_confirm(self):
        """合成数据：T 触发，T+1/T+2/T+3 份额均 > ma20 → 应输出信号"""
        dates = [pd.Timestamp('2025-01-01') + pd.Timedelta(days=i) for i in range(30)]
        closes = [10.0] * 30
        closes[25] = 9.0
        shares = [100.0] * 30
        shares[25] = 200.0  # T: spike
        shares[26] = 110.0  # T+1: OK
        shares[27] = 115.0  # T+2: OK
        shares[28] = 120.0  # T+3: OK

        fake = _build_etf_data_fake(shares, closes, dates)
        fake['FAKE'] = _compute_indicators(fake['FAKE'])

        single = generate_signals(fake, mode='single_day')
        confirmed = generate_signals(fake, mode='confirmed', confirm_days=3)

        self.assertEqual(len(single), 1)
        self.assertEqual(len(confirmed), 1,
                         "方案 A 应输出：3 天全部确认")
        self.assertEqual(confirmed[0]['signal_date'], dates[28],
                         "信号输出日应为 T+3")

    def test_window_insufficient(self):
        """窗口不足 20 天：rolling 产生 NaN，不应触发"""
        dates = [pd.Timestamp('2025-01-01') + pd.Timedelta(days=i) for i in range(15)]
        closes = [10.0] * 15
        shares = [100.0] * 14 + [200.0]  # 最后一天 spike

        fake = _build_etf_data_fake(shares, closes, dates)
        fake['FAKE'] = _compute_indicators(fake['FAKE'])

        single = generate_signals(fake, mode='single_day')
        self.assertEqual(len(single), 0,
                         "不足 20 天窗口 → share_z_20 = NaN → 无触发")

    def test_dual_trigger(self):
        """双触发：两个独立的触发日，方案 A 应输出 2 个"""
        dates = [pd.Timestamp('2025-01-01') + pd.Timedelta(days=i) for i in range(60)]
        closes = [10.0] * 60
        # T1 = 第 25 天: spike + price drop
        closes[25] = 9.0
        # T2 = 第 40 天 (足够远, 不受 T1 影响): spike + price drop
        closes[40] = 9.0
        shares = [100.0] * 60
        shares[25] = 300.0  # 更大 spike
        shares[40] = 300.0
        # 两个触发后 3 天份额大幅高于均线（均线约 100，设为 250）
        shares[26:29] = [250.0] * 3
        shares[41:44] = [250.0] * 3

        fake = _build_etf_data_fake(shares, closes, dates)
        fake['FAKE'] = _compute_indicators(fake['FAKE'])

        single = generate_signals(fake, mode='single_day')
        confirmed = generate_signals(fake, mode='confirmed', confirm_days=3)

        self.assertEqual(len(single), 2, "应检测到 2 个触发")
        self.assertEqual(len(confirmed), 2, "两个都应通过确认")
        self.assertTrue(
            pd.Timestamp(confirmed[0]['signal_date']) <
            pd.Timestamp(confirmed[1]['signal_date']),
            "第二个信号的输出日应晚于第一个")


class TestRealDataConsistency(unittest.TestCase):
    """与 etf.db 真实数据的一致性测试"""

    def test_single_day_count_39(self):
        """单日报复现冻结 39 个 E2 信号"""
        sigs = generate_signals(etf_data, mode='single_day')
        self.assertEqual(len(sigs), 39, f"应复现 39 个信号，得到 {len(sigs)}")

    def test_single_day_distribution(self):
        """单日报分布与冻结一致"""
        from collections import Counter
        sigs = generate_signals(etf_data, mode='single_day')
        dist = Counter(s['code'] for s in sigs)
        expected = {'159228': 1, '159307': 8, '159525': 6, '159547': 3,
                    '159549': 6, '512890': 6, '563020': 9}
        for code, count in expected.items():
            self.assertEqual(dist.get(code, 0), count,
                             f"{code}: 预期 {count}, 得到 {dist.get(code, 0)}")

    def test_confirmed_entries_shifted_by_3(self):
        """方案 A 的 entry 日期 = 单日报 trigger 日期 + 3 天"""
        single = generate_signals(etf_data, mode='single_day')
        confirmed = generate_signals(etf_data, mode='confirmed', confirm_days=3)

        # 所有 39 个 single 触发日都应该出现在 confirmed 的 trigger_date 中
        # （当前数据 0 过滤，所以全部通过）
        single_set = {str(s['signal_date'])[:10] for s in single}
        confirmed_trigger_set = {str(c['trigger_date'])[:10] for c in confirmed}
        self.assertTrue(
            single_set.issubset(confirmed_trigger_set),
            f"所有 single 触发日应出现在 confirmed trigger_date 中。"
            f"缺失: {single_set - confirmed_trigger_set}")

        # signal_date 应比 trigger_date 晚 2-5 天（交易日间隔，含周末）
        for c in confirmed:
            td_dt = pd.Timestamp(str(c['trigger_date'])[:10])
            sd_dt = pd.Timestamp(str(c['signal_date'])[:10])
            delta = (sd_dt - td_dt).days
            self.assertTrue(2 <= delta <= 7,
                            f"{c['code']} T={str(c['trigger_date'])[:10]} "
                            f"T+3={str(c['signal_date'])[:10]} delta={delta} 天")

    def test_159307_20250407_spot_check(self):
        """159307 2025-04-07 触发日 → T+1..T+3 份额 > shares_ma20"""
        # 从 etf.db 读取 159307 的 shares 和 shares_ma20
        code = '159307'
        rows = scale_df[scale_df['code'] == code].copy()
        rows = rows.sort_values('date')

        # 计算 shares_ma20
        rows['shares_ma20'] = rows['shares'].rolling(20, min_periods=20).mean()

        # 触发日 T
        t_val = pd.Timestamp('2025-04-07')
        t_row = rows[rows['date'] == t_val]
        self.assertTrue(len(t_row) > 0, "T 日应有数据")

        # 找到 T 在 index 中的位置
        t_i = rows.index[rows['date'] == t_val][0]

        for k in [1, 2, 3]:
            c_i = t_i + k
            if c_i < len(rows):
                c_row = rows.iloc[c_i]
                # 真实数据应通过确认（当前数据集 0 过滤）
                self.assertGreater(c_row['shares'], c_row['shares_ma20'],
                                   f"{code} T+{k}={c_row['date'].strftime('%Y-%m-%d')} "
                                   f"shares={c_row['shares']:.0f} > ma20={c_row['shares_ma20']:.0f}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
