from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from etf_longhubang.calendar import TradingCalendar
from etf_longhubang.content import build_payload
from etf_longhubang.data import load_snapshot
from etf_longhubang.data import refresh_scale
from etf_longhubang.errors import InsufficientDataError, PackageValidationError, SourceUnavailableError
from etf_longhubang.html import render_pages
from etf_longhubang.publish import existing_is_identical, make_stage, publish_stage, write_skip
from etf_longhubang.utils import atomic_json, atomic_text, format_share_change, sha256_file, spoken_name, spoken_share_change
from etf_longhubang.validation import validate_business, validate_html_security, validate_schema
from etf_longhubang.visual_qa import run_visual_qa


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "docs" / "schemas" / "etf_longhubang.v1.schema.json"


def fixture_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE funds(code TEXT PRIMARY KEY,name TEXT NOT NULL,market TEXT NOT NULL);
        CREATE TABLE fund_scale(date TEXT NOT NULL,code TEXT NOT NULL,shares REAL NOT NULL,PRIMARY KEY(date,code));
        """
    )
    changes = [500_000_000, 400_000_000, 300_000_000, 200_000_000, 100_000_000, 50_000_000]
    for index, change in enumerate(changes, 1):
        code = f"51{index:04d}" if index <= 3 else f"15{index:04d}"
        market = "上海" if index <= 3 else "深圳"
        prior = 1_000_000_000
        conn.execute("INSERT INTO funds VALUES(?,?,?)", (code, f"增加{index}ETF", market))
        conn.execute("INSERT INTO fund_scale VALUES(?,?,?)", ("2026-10-22", code, prior))
        conn.execute("INSERT INTO fund_scale VALUES(?,?,?)", ("2026-10-23", code, prior + change))
    for index, change in enumerate(changes, 1):
        code = f"56{index:04d}" if index <= 3 else f"16{index:04d}"
        market = "上海" if index <= 3 else "深圳"
        prior = 2_000_000_000
        conn.execute("INSERT INTO funds VALUES(?,?,?)", (code, f"减少{index}ETF", market))
        conn.execute("INSERT INTO fund_scale VALUES(?,?,?)", ("2026-10-22", code, prior))
        conn.execute("INSERT INTO fund_scale VALUES(?,?,?)", ("2026-10-23", code, prior - change))
    conn.commit()
    conn.close()


class ProducerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "etf.db"
        fixture_db(self.db)
        self.snapshot = load_snapshot(self.db, "2026-10-23")
        self.payload = build_payload(self.snapshot, "etf_longhubang_20261023_v1", "2026-10-24", "2026-10-24T06:05:00+08:00")
        self.pages = render_pages(self.payload)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_share_ranking_uses_absolute_change_and_market_suffix(self) -> None:
        self.assertEqual([row["rank"] for row in self.snapshot.increase], [1, 2, 3, 4, 5])
        self.assertEqual(self.snapshot.increase[0]["share_change"], 500_000_000)
        self.assertEqual(self.snapshot.decrease[0]["share_change"], -500_000_000)
        self.assertTrue(self.snapshot.increase[0]["fund_code"].endswith(".SH"))
        self.assertEqual(self.snapshot.audit["common_coverage"], 1.0)

    def test_zero_previous_percentage_is_null_and_ties_use_code_order(self) -> None:
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE fund_scale SET shares=0 WHERE date='2026-10-22' AND code='510001'")
        conn.execute("UPDATE fund_scale SET shares=600000000 WHERE date='2026-10-23' AND code='510001'")
        conn.execute("UPDATE fund_scale SET shares=1400000000 WHERE date='2026-10-23' AND code IN ('510002','510003')")
        conn.commit(); conn.close()
        snapshot = load_snapshot(self.db, "2026-10-23")
        self.assertIsNone(snapshot.increase[0]["share_change_pct"])
        tied = [row["fund_code"] for row in snapshot.increase if row["share_change"] == 400_000_000]
        self.assertEqual(tied, sorted(tied))

    def test_missing_exchange_and_short_ranking_are_rejected(self) -> None:
        conn = sqlite3.connect(self.db)
        conn.execute("DELETE FROM fund_scale WHERE date='2026-10-23' AND code LIKE '15%'")
        conn.execute("DELETE FROM fund_scale WHERE date='2026-10-23' AND code LIKE '16%'")
        conn.commit(); conn.close()
        with self.assertRaises(InsufficientDataError):
            load_snapshot(self.db, "2026-10-23")

    def test_refresh_nonzero_exit_is_source_unavailable(self) -> None:
        fake = type("Result", (), {"returncode": 2, "stdout": "failed", "stderr": "network down"})()
        with patch("etf_longhubang.data.subprocess.run", return_value=fake):
            with self.assertRaises(SourceUnavailableError):
                refresh_scale(Path("python"), self.root, "2026-10-23", self.root / "refresh.log")

    def test_display_and_spoken_units_are_deterministic(self) -> None:
        self.assertEqual(format_share_change(125_000_000), "+1.25亿份")
        self.assertEqual(format_share_change(-98_000_000), "-9800万份")
        self.assertEqual(spoken_share_change(125_000_000), "一点二五亿份")

    def test_schema_business_and_html_security(self) -> None:
        validate_schema(self.payload, SCHEMA)
        report = validate_business(self.payload, self.pages)
        self.assertGreater(report["estimated_narration_seconds"], 0)
        self.assertEqual(validate_html_security(self.pages)["status"], "ok")

    def test_requested_page_order_and_narration_copy(self) -> None:
        self.assertIn('<h1 class="cover-title">ETF份额龙虎榜</h1>', self.pages["page_01_cover.html"])
        self.assertNotIn("ETF<br>", self.pages["page_01_cover.html"])
        self.assertIn('class="page cover-page"', self.pages["page_01_cover.html"])
        self.assertIn("Global速览(ETF龙虎榜)", self.pages["page_01_cover.html"])
        self.assertIn("top:240px", self.pages["page_01_cover.html"])
        self.assertIn("height:1440px", self.pages["page_01_cover.html"])
        for page_index, side, filename in ((1, "increase", "page_02_increase.html"), (2, "decrease", "page_03_decrease.html")):
            html = self.pages[filename]
            positions = [html.index(f'<div class="rank-no">{rank}</div>') for rank in range(1, 6)]
            self.assertEqual(positions, sorted(positions))
            narration = self.payload["pages"][page_index]["tts_text"]
            for item in self.payload["rankings"][side]:
                self.assertIn(spoken_name(item["fund_name"]), narration)
                self.assertNotIn(item["display_value"][1:], narration)
        self.assertEqual(self.payload["pages"][3]["headline"], "今日数据结论")
        self.assertIn("份额变化不等于资金净流入", self.payload["pages"][3]["tts_text"])
        self.assertIn("不代表价格涨跌", self.payload["pages"][3]["tts_text"])
        self.assertIn("不构成投资建议", self.payload["pages"][3]["tts_text"])
        self.assertTrue(
            all(
                not sentence.endswith("。")
                for page in self.payload["pages"]
                for sentence in page["subtitle_sentences"]
            )
        )

    def test_example_contract_still_validates(self) -> None:
        example = ROOT / "docs" / "examples" / "etf_longhubang" / "etf_longhubang.json"
        validate_schema(json.loads(example.read_text(encoding="utf-8")), SCHEMA)

    def test_visual_qa_outputs_four_screenshots(self) -> None:
        package = self.root / "package"
        package.mkdir()
        for name, value in self.pages.items():
            atomic_text(package / name, value)
        report = run_visual_qa(package, self.root / "qa", list(self.pages))
        self.assertEqual(report["status"], "ok")
        for filename in (
            "preview_cover.png",
            "preview_increase.png",
            "preview_decrease.png",
            "preview_close.png",
            "preview_contact_sheet.png",
        ):
            self.assertTrue((self.root / "qa" / filename).exists(), filename)
        self.assertEqual(report["render_mode"], "render")
        self.assertTrue(all(page["metrics"]["runningAnimations"] == 0 for page in report["pages"]))

    def test_visual_language_separates_share_change_from_price_colors(self) -> None:
        html = self.pages["page_02_increase.html"]
        self.assertIn("--share-up:#2fd3c5", html)
        self.assertIn("--share-down:#f2b84b", html)
        self.assertIn("--price-up:#ef5b67", html)
        self.assertIn("--price-down:#39b77a", html)
        self.assertIn("自制份额统计榜", html)
        self.assertIn("dataset.renderMode", html)

    def test_long_fund_name_remains_inside_ranking_row(self) -> None:
        payload = json.loads(json.dumps(self.payload, ensure_ascii=False))
        payload["rankings"]["increase"][0]["fund_name"] = "超长基金名称用于验证页面不会静默溢出边界的交易型开放式指数基金"
        pages = render_pages(payload)
        package = self.root / "long_name"; package.mkdir()
        for name, value in pages.items(): atomic_text(package / name, value)
        report = run_visual_qa(package, self.root / "long_name_qa", list(pages))
        self.assertEqual(report["status"], "ok")

    def test_missing_render_marker_is_rejected_before_browser(self) -> None:
        broken = dict(self.pages)
        broken["page_01_cover.html"] = broken["page_01_cover.html"].replace("dataset.renderReady", "dataset.notReady")
        with self.assertRaises(PackageValidationError):
            validate_html_security(broken)

    def test_ready_is_written_after_five_content_files_and_is_idempotent(self) -> None:
        output = self.root / "ETF"
        archive = self.root / "ETF_archive"
        output.mkdir()
        stage = make_stage(output, "20261023", 1)
        atomic_json(stage / "etf_longhubang.json", self.payload)
        for name, value in self.pages.items():
            atomic_text(stage / name, value)
        final = publish_stage(stage, output, archive, self.payload)
        ready = json.loads((final / "_READY.json").read_text(encoding="utf-8"))
        self.assertEqual(ready["file_count"], 5)
        self.assertEqual(ready["main_file_sha256"], sha256_file(final / "etf_longhubang.json"))
        self.assertTrue(existing_is_identical(final, self.payload))
        newest_content_mtime = max(path.stat().st_mtime_ns for path in final.iterdir() if path.name != "_READY.json")
        self.assertGreaterEqual((final / "_READY.json").stat().st_mtime_ns, newest_content_mtime)
        ready["main_file_sha256"] = "0" * 64
        atomic_json(final / "_READY.json", ready)
        self.assertFalse(existing_is_identical(final, self.payload))

    def test_revision_archives_the_previous_package(self) -> None:
        output = self.root / "ETF"; archive = self.root / "ETF_archive"; output.mkdir()
        stage1 = make_stage(output, "20261023", 1)
        atomic_json(stage1 / "etf_longhubang.json", self.payload)
        for name, value in self.pages.items(): atomic_text(stage1 / name, value)
        publish_stage(stage1, output, archive, self.payload)
        payload2 = json.loads(json.dumps(self.payload, ensure_ascii=False))
        payload2["package_id"] = "etf_longhubang_20261023_v2"
        stage2 = make_stage(output, "20261023", 2)
        atomic_json(stage2 / "etf_longhubang.json", payload2)
        for name, value in self.pages.items(): atomic_text(stage2 / name, value)
        publish_stage(stage2, output, archive, payload2)
        self.assertTrue((archive / "20261023" / "etf_longhubang_20261023_v1" / "_READY.json").exists())
        current = json.loads((output / "20261023" / "_READY.json").read_text(encoding="utf-8"))
        self.assertEqual(current["package_id"], "etf_longhubang_20261023_v2")

    def test_cached_calendar_and_skip_receipt(self) -> None:
        cache = self.root / "calendar.json"
        atomic_json(cache, {"range_start": "2026-10-01", "range_end": "2026-10-31", "trading_dates": ["2026-10-23"]})
        calendar = TradingCalendar(cache)
        self.assertTrue(calendar.is_trading_day(date(2026, 10, 23)))
        self.assertFalse(calendar.is_trading_day(date(2026, 10, 24)))
        output = self.root / "ETF"
        output.mkdir()
        result = write_skip(output, self.root / "archive", "2026-10-24", "non_trading_day", "非交易日")
        payload = json.loads((result / "_SKIP.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["reason_code"], "non_trading_day")
        self.assertFalse((result / "_READY.json").exists())


if __name__ == "__main__":
    unittest.main()

