from __future__ import annotations

from pathlib import Path
import json
import sqlite3

import db
from sync.etf_sync.config import SyncConfig
from sync.etf_sync.local_store import materialize, verify
from sync.etf_sync.video_cli import build_payload, export_pages


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
        "SELECT change_rate,streak_days,anomaly_ratio,anomaly_status FROM etf_derived_daily WHERE date='2026-01-25' AND code='510001'"
    ).fetchone()
    assert latest[0] > 0
    assert latest[1] == 0
    assert latest[2] is None
    assert latest[3] == "SHARE_SCALE_BELOW_THRESHOLD"
    missing = conn.execute(
        "SELECT change_status,streak_days FROM etf_derived_daily WHERE date='2026-01-25' AND code='159001'"
    ).fetchone()
    assert missing == ("BASELINE_MISSING", 0)
    assert conn.execute("SELECT COUNT(*) FROM etf_derived_daily_stage").fetchone()[0] == 0
    coverage = json.loads(conn.execute(
        "SELECT coverage_json FROM etf_calc_publish_status WHERE id='current'"
    ).fetchone()[0])
    assert coverage["total_shares"] == 198
    assert coverage["increase_count"] == 1
    assert coverage["decrease_count"] == 0
    assert coverage["unchanged_count"] == 0
    assert coverage["unavailable_change_count"] == 1
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


def test_anomaly_ranking_requires_share_scale_over_100_yi(tmp_path: Path):
    path = tmp_path / "etf.db"
    make_database(path)
    conn = sqlite3.connect(path)
    # 放大最后一日变化，使其满足历史倍数和变化率条件；份额本身仍远低于100亿份。
    conn.execute("UPDATE fund_scale SET shares=250 WHERE date='2026-01-25' AND code='510001'")
    conn.commit()
    conn.close()
    materialize(SyncConfig(db_path=path), full=True)
    conn = sqlite3.connect(path)
    qualified, status, ratio = conn.execute(
        "SELECT anomaly_qualified,anomaly_status,anomaly_ratio FROM etf_derived_daily "
        "WHERE date='2026-01-25' AND code='510001'"
    ).fetchone()
    assert ratio is None
    assert (qualified, status) == (0, "SHARE_SCALE_BELOW_THRESHOLD")
    conn.close()


def test_ranking_materialization_keeps_only_top_100_per_direction(tmp_path: Path):
    path = tmp_path / "etf.db"
    make_database(path)
    conn = sqlite3.connect(path)
    for index in range(101):
        code = f"{700000 + index:06d}"
        conn.execute("INSERT INTO fund_scale VALUES(?,?,?)", ("2026-01-24", code, 11_000_000_000))
        conn.execute("INSERT INTO fund_scale VALUES(?,?,?)", ("2026-01-25", code, 11_000_000_000 + index + 1))
    conn.commit()
    conn.close()
    materialize(SyncConfig(db_path=path), full=True)
    conn = sqlite3.connect(path)
    assert conn.execute(
        "SELECT COUNT(*) FROM etf_derived_daily WHERE date='2026-01-25' "
        "AND direction='increase' AND delta_rank IS NOT NULL"
    ).fetchone()[0] == 100
    assert conn.execute(
        "SELECT COUNT(*) FROM etf_derived_daily WHERE date='2026-01-25' "
        "AND direction='increase' AND delta_rank IS NULL"
    ).fetchone()[0] >= 1
    conn.close()


def test_video_payload_uses_published_batch(tmp_path: Path):
    path = tmp_path / "etf.db"
    make_database(path)
    materialize(SyncConfig(db_path=path), full=True)
    content, audit = build_payload(path)
    assert content["trade_date"] == "2026-01-25"
    assert content["batch_id"].startswith("local-")
    assert audit["publishable"] is True
    pages = export_pages(tmp_path / "video", "http://127.0.0.1:5010", content,
                         ("daily-increase", "detail"))
    assert set(pages) == {"increase", "focus"}
    assert "ETF份额龙虎榜" in Path(pages["increase"]).read_text(encoding="utf-8")
