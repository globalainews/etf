from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

import app as etf_app
from design.etf_rankings.prototype.data_engine import build_content
from src.etf_rankings.ranking_service import RankingInputError, build_rankings


DB_PATH = Path(__file__).resolve().parents[1] / "etf.db"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class RankingsServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.before_hash = sha256(DB_PATH)
        cls.payload = build_rankings("2026-10-23")
        cls.content, _ = build_content("2026-10-23")
        cls.after_hash = sha256(DB_PATH)

    def test_database_is_unchanged(self) -> None:
        self.assertEqual(self.before_hash, self.after_hash)

    def test_current_database_baseline(self) -> None:
        self.assertEqual(self.payload["audit"]["scale_count"], 1690)
        self.assertEqual(
            self.payload["audit"]["price_match_count"] + self.payload["audit"]["missing_price_count"],
            self.payload["audit"]["scale_count"],
        )
        first = self.payload["daily"]["increase"][0]
        self.assertEqual(first["code"], "510300")
        self.assertAlmostEqual(first["reference_value_yi"], 104.985, places=3)

    def test_daily_and_weekly_shapes(self) -> None:
        daily = self.payload["daily"]
        self.assertEqual(sum(daily["counts"].values()), 1690)
        self.assertTrue(all(row["reference_value_yi"] > 0 for row in daily["increase"]))
        self.assertTrue(all(row["reference_value_yi"] < 0 for row in daily["decrease"]))
        weekly = self.payload["weekly"]["continuous_increase"]
        self.assertTrue(weekly)
        self.assertTrue(all(row["positive_days_5"] == 5 for row in weekly))
        self.assertTrue(all(len(row["series_5"]) == 6 for row in weekly))

    def test_invalid_date_is_rejected(self) -> None:
        with self.assertRaises(RankingInputError):
            build_rankings("2026/10/23")

    def test_video_reuses_daily_weekly_service(self) -> None:
        self.assertEqual(
            [row["code"] for row in self.content["rankings"]["daily_reference_increase"]],
            [row["code"] for row in self.payload["daily"]["increase"]],
        )
        self.assertEqual(
            [row["code"] for row in self.content["rankings"]["continuous_increase_5d"]],
            [row["code"] for row in self.payload["weekly"]["continuous_increase"]],
        )

    def test_588000_historical_regression(self) -> None:
        focus = self.content["focus"]["588000"]
        self.assertAlmostEqual(focus["share_change_20d_pct"], 48.03, places=2)
        self.assertAlmostEqual(focus["price_change_20d_pct"], -16.16, places=2)


class RankingsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = etf_app.app.test_client()

    def test_default_and_selected_date(self) -> None:
        latest = self.client.get("/api/rankings")
        selected = self.client.get("/api/rankings?date=2026-10-23")
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json["meta"]["trade_date"], "2026-10-23")
        self.assertIn("daily", selected.json)
        self.assertIn("weekly", selected.json)

    def test_invalid_and_missing_date(self) -> None:
        self.assertEqual(self.client.get("/api/rankings?date=bad").status_code, 400)
        self.assertEqual(self.client.get("/api/rankings?date=2000-01-01").status_code, 404)

    def test_existing_scale_movers_route_still_works(self) -> None:
        response = self.client.get("/api/scale-movers?date=2026-10-23")
        self.assertEqual(response.status_code, 200)
        self.assertIn("gainers", response.json)


if __name__ == "__main__":
    unittest.main()
