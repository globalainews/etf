import unittest

import numpy as np
import pandas as pd

from research.three_etf_validation import CODES, simulate


class SimulatorTimingTest(unittest.TestCase):
    def test_close_signal_fills_next_open_and_does_not_rebalance_daily(self):
        dates = pd.Index(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"])
        op = pd.DataFrame(100.0, index=dates, columns=CODES)
        op.loc["2026-01-03":, "512890"] = 110.0
        vol = pd.DataFrame(1.0, index=dates, columns=CODES)
        target = pd.DataFrame(np.nan, index=dates, columns=CODES)
        target.loc[dates[0]] = [0.5, 0.0, 0.0]
        frame = simulate(op, vol, target, 0.001)
        self.assertEqual(frame.index.tolist(), dates[1:].tolist())
        self.assertAlmostEqual(frame.loc[dates[1], "nav"], 1.0)
        self.assertAlmostEqual(frame.loc[dates[2], "nav"], (1 - 0.0005) * 1.05)
        self.assertAlmostEqual(frame.loc[dates[3], "turnover"], 0.0)

    def test_untradeable_open_rejects_fill(self):
        dates = pd.Index(["2026-01-01", "2026-01-02", "2026-01-03"])
        op = pd.DataFrame(100.0, index=dates, columns=CODES)
        vol = pd.DataFrame(1.0, index=dates, columns=CODES)
        vol.loc[dates[1], "512890"] = 0
        target = pd.DataFrame(np.nan, index=dates, columns=CODES)
        target.loc[dates[0]] = [1.0, 0.0, 0.0]
        frame = simulate(op, vol, target, 0.001)
        self.assertEqual(frame.turnover.sum(), 0)
        self.assertEqual(frame.nav.iloc[-1], 1)


if __name__ == "__main__":
    unittest.main()
