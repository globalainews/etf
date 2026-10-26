from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from sync.etf_sync.compute import anomaly_ratio, build_release_bundle, change_rate, stable_rank
from sync.etf_sync.config import SyncConfig


def make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE funds(code TEXT PRIMARY KEY,name TEXT,type TEXT,market TEXT,issue_year INTEGER,theme TEXT);
        CREATE TABLE fund_scale(date TEXT,code TEXT,shares REAL,PRIMARY KEY(date,code));
        CREATE TABLE fund_price(date TEXT,code TEXT,close REAL,volume REAL,PRIMARY KEY(date,code));
        CREATE TABLE signal_events(date TEXT,code TEXT,event TEXT,close REAL,share_z_20 REAL,price_dev_20 REAL,PRIMARY KEY(date,code));
    """)
    conn.executemany("INSERT INTO funds VALUES(?,?,?,?,?,?)", [
        ("510001", "测试增加ETF", "跨市", "上海", 2020, "宽基"),
        ("159001", "测试缺失ETF", "", "深圳", 2021, ""),
    ])
    for index in range(25):
        date = f"2026-01-{index + 1:02d}"
        base = 100.0 + index
        conn.execute("INSERT INTO fund_scale VALUES(?,?,?)", (date, "510001", base))
        conn.execute("INSERT INTO fund_price VALUES(?,?,?,?)", (date, "510001", 1 + index / 100, None))
        if index != 23:  # explicit missing point interrupts continuity
            conn.execute("INSERT INTO fund_scale VALUES(?,?,?)", (date, "159001", 50.0,))
    conn.commit()
    conn.close()


def test_change_rate_invalid_baseline():
    assert change_rate(10, 0) == (None, "BASELINE_INVALID")
    assert change_rate(10, None) == (None, "BASELINE_MISSING")
    value, status = change_rate(12, 10)
    assert value == pytest.approx(0.2)
    assert status == "OK"


def test_anomaly_window_excludes_current_by_contract():
    ratio, status = anomaly_ratio(0.10, [0.01] * 20, 20)
    assert status == "OK"
    assert ratio == pytest.approx(10)
    ratio, status = anomaly_ratio(0.10, [0.01] * 19, 20)
    assert ratio is None and status == "HISTORY_INSUFFICIENT"


def test_stable_rank_ties_use_code_order():
    rows = [{"code": "2", "v": 1}, {"code": "1", "v": 1}, {"code": "3", "v": 0}]
    result = stable_rank(rows, "v", True)
    assert [row["code"] for row in result] == ["1", "2", "3"]
    assert [row["rank"] for row in result] == [1, 1, 3]


def test_bundle_uses_actual_dates_and_marks_missing(tmp_path: Path):
    db_path = tmp_path / "sample.db"
    make_db(db_path)
    bundle = build_release_bundle(SyncConfig(db_path=db_path), initial=True)
    assert bundle["release"]["statistic_date"] == "2026-01-25"
    assert bundle["release"]["baseline_date"] == "2026-01-24"
    missing = next(row for row in bundle["collections"]["ranking_snapshots"] if row["code"] == "159001")
    assert missing["change_rate"] is None
    assert missing["change_status"] == "BASELINE_MISSING"
    streak = next(row for row in bundle["collections"]["streak_snapshots"] if row["code"] == "159001")
    assert streak["days"] == 0


def test_correction_boundary_creates_revision_documents(tmp_path: Path):
    db_path = tmp_path / "sample.db"
    make_db(db_path)
    config = SyncConfig(db_path=db_path)
    daily = build_release_bundle(config, release_seq=2)
    correction = build_release_bundle(config, release_seq=3, correction_from="2026-01-20")
    assert {row["date"] for row in daily["collections"]["etf_history"]} == {"2026-01-25"}
    assert min(row["date"] for row in correction["collections"]["etf_history"]) == "2026-01-20"
    assert all(row["release_seq"] == 3 for row in correction["collections"]["etf_history"])

