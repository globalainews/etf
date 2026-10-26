from __future__ import annotations

from pathlib import Path
import sqlite3

import db
from sync.etf_sync.config import SyncConfig
from sync.etf_sync.local_store import materialize, verify


def make_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    db.ensure_schema(conn)
    conn.executemany("INSERT INTO funds(code,name,type,market,issue_year,theme) VALUES(?,?,?,?,?,?)", [
        ("510001", "增加ETF", "跨市", "上海", 2020, "宽基"),
        ("159001", "缺失ETF", "", "深圳", 2021, "宽基"),
    ])
    for index in range(25):
        day = f"2026-01-{index + 1:02d}"
        conn.execute("INSERT INTO fund_scale VALUES(?,?,?)", (day, "510001", 100 + index))
        conn.execute("INSERT INTO fund_price VALUES(?,?,?,?)", (day, "510001", 1 + index / 100, None))
        if index != 23:
            conn.execute("INSERT INTO fund_scale VALUES(?,?,?)", (day, "159001", 50 + index,))
    conn.commit()
    conn.close()


def test_full_materialization_publishes_all_dates(tmp_path: Path):
    path = tmp_path / "etf.db"
    make_database(path)
    result = materialize(SyncConfig(db_path=path), full=True)
    assert result["published"] is True
    assert verify(path)["ok"] is True
    conn = sqlite3.connect(path)
    latest = conn.execute(
        "SELECT change_rate,streak_days,anomaly_ratio FROM etf_derived_daily WHERE date='2026-01-25' AND code='510001'"
    ).fetchone()
    assert latest[0] > 0
    assert latest[1] == 24
    assert latest[2] is not None
    missing = conn.execute(
        "SELECT change_status,streak_days FROM etf_derived_daily WHERE date='2026-01-25' AND code='159001'"
    ).fetchone()
    assert missing == ("BASELINE_MISSING", 0)
    assert conn.execute("SELECT COUNT(*) FROM etf_derived_daily_stage").fetchone()[0] == 0
    conn.close()


def test_correction_is_idempotent_and_updates_release(tmp_path: Path):
    path = tmp_path / "etf.db"
    make_database(path)
    config = SyncConfig(db_path=path)
    materialize(config, full=True)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE fund_scale SET shares=150 WHERE date='2026-01-25' AND code='510001'")
    conn.commit()
    conn.close()
    materialize(config, correction_from="2026-01-25")
    materialize(config, correction_from="2026-01-25")
    conn = sqlite3.connect(path)
    assert conn.execute(
        "SELECT COUNT(*) FROM etf_derived_daily WHERE date='2026-01-25' AND code='510001'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT delta_shares FROM etf_derived_daily WHERE date='2026-01-25' AND code='510001'"
    ).fetchone()[0] == 27
    conn.close()
