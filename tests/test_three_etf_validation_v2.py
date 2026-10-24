import unittest

import numpy as np
import pandas as pd

from research.three_etf_validation_v2 import CODES, load_data, metrics, simulate


class SplitAndAccountingTest(unittest.TestCase):
    def test_audited_split_adjustment_and_blackout(self):
        _, _, _, reported = load_data()
        # 2-session lag: last observation before the 2021 split stays on the
        # post-split unit; the event bar itself becomes unavailable.
        self.assertAlmostEqual(reported.loc["2021-10-21", "512890"], 138110800, delta=100)
        self.assertTrue(np.isnan(reported.loc["2021-10-26", "512890"]))
        self.assertAlmostEqual(reported.loc["2026-07-01", "512480"], 17022008000, delta=1e7)
        self.assertTrue(np.isnan(reported.loc["2026-07-06", "512480"]))

    def test_first_loss_counts_in_drawdown(self):
        dates = pd.Index([f"2026-01-{i:02d}" for i in range(1, 12)])
        curve = pd.DataFrame({"nav": [0.9] * len(dates), "turnover": 0., "fee": 0.,
                              **{f"w_{c}": 0. for c in CODES}}, index=dates)
        self.assertAlmostEqual(metrics(curve, dates[0], dates[-1])["max_dd"], -.1)

    def test_next_open_and_no_daily_rebalance(self):
        dates = pd.Index([f"2026-01-{i:02d}" for i in range(1, 28)])
        op = pd.DataFrame(100., index=dates, columns=CODES)
        op.loc[dates[22]:, "512890"] = 110.
        vol = pd.DataFrame(1., index=dates, columns=CODES)
        target = pd.DataFrame(np.nan, index=dates, columns=CODES)
        target.loc[dates[20]] = [.5, 0., 0.]
        curve = simulate(op, vol, target, .001)
        self.assertAlmostEqual(curve.loc[dates[22], "turnover"], .5)
        self.assertAlmostEqual(curve.loc[dates[22], "nav"], (1 - .0005) * 1.05)
        self.assertAlmostEqual(curve.loc[dates[23], "turnover"], 0)
        self.assertAlmostEqual(curve.loc[dates[22], "return"], (1 - .0005) * 1.05 - 1)


if __name__ == "__main__":
    unittest.main()
