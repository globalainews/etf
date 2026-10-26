from __future__ import annotations

import unittest

from prototype.data_engine import DB_PATH, asset_class, build_content, file_sha256, pct, robust_z


class MetricUnitTests(unittest.TestCase):
    def test_pct(self) -> None:
        self.assertAlmostEqual(pct(120, 100), 20.0)
        self.assertIsNone(pct(1, 0))

    def test_robust_z_mad_zero(self) -> None:
        history = [2.0] * 25
        self.assertEqual(robust_z(2.0, history), 0.0)
        self.assertIsNone(robust_z(3.0, history))

    def test_new_fund_history_is_insufficient(self) -> None:
        self.assertIsNone(robust_z(3.0, [1.0] * 19))

    def test_classification_isolated_from_database(self) -> None:
        self.assertEqual(asset_class("黄金ETF", "商品", "000000", {}), "commodity")
        self.assertEqual(asset_class("纳指ETF", "跨境/海外", "000001", {}), "cross_border")
        self.assertEqual(asset_class("短融ETF", "债券/货币", "000002", {}), "bond")


class CurrentDatabaseIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hash_before = file_sha256(DB_PATH)
        cls.content, cls.audit = build_content("2026-10-23")
        cls.hash_after = file_sha256(DB_PATH)

    def test_database_is_read_only(self) -> None:
        self.assertEqual(self.hash_before, self.hash_after)

    def test_locked_counts(self) -> None:
        self.assertEqual(self.audit["table_counts"]["funds"], 1801)
        self.assertEqual(self.audit["latest_scale_count"], 1690)
        self.assertEqual(self.audit["latest_overlap_count"], 1543)
        self.assertEqual(self.audit["latest_missing_price_count"], 147)

    def test_reference_value_formula(self) -> None:
        first = self.content["rankings"]["daily_reference_increase"][0]
        expected = first["delta_shares_yi"] * first["close"]
        self.assertAlmostEqual(first["reference_value_yi"], expected, places=2)

    def test_five_day_continuity(self) -> None:
        rows = self.content["rankings"]["continuous_increase_5d"]
        self.assertTrue(rows)
        self.assertTrue(all(row["positive_days_5"] == 5 for row in rows))
        self.assertTrue(all(len(row["series_5"]) == 6 for row in rows))

    def test_weekly_decrease_uses_full_population(self) -> None:
        rows = self.content["rankings"]["weekly_decrease_5d"]
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(row["delta_5d_shares_yi"] < 0 for row in rows))
        self.assertEqual(rows, sorted(rows, key=lambda row: row["reference_5d_yi"]))

    def test_focus_and_rejected_case(self) -> None:
        focus = self.content["focus"]["588000"]
        self.assertAlmostEqual(focus["share_change_20d_pct"], 48.03, places=2)
        self.assertAlmostEqual(focus["price_change_20d_pct"], -16.16, places=2)
        self.assertEqual(len(focus["series_20"]), 21)
        self.assertEqual(self.content["rejected_case"]["code"], "159516")

    def test_missing_price_is_not_ranked(self) -> None:
        rows = self.content["rankings"]["daily_reference_increase"]
        self.assertTrue(all(row["close"] is not None for row in rows))

    def test_theme_ranking_is_blocked(self) -> None:
        self.assertEqual(self.content["rankings"]["theme"]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
