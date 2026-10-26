from __future__ import annotations

from pathlib import Path

import db
from sync.etf_sync.config import SyncConfig
from sync.etf_sync.local_store import materialize
from tests.test_local_materialization import make_database


def test_web_api_and_watchlist(tmp_path: Path, monkeypatch):
    path = tmp_path / "etf.db"
    make_database(path)
    materialize(SyncConfig(db_path=path), full=True)
    monkeypatch.setattr(db, "DB_PATH", str(path))
    import app as etf_app
    client = etf_app.app.test_client()

    assert client.get("/ranking-app").status_code == 200
    assert client.get("/ranking-video").status_code == 200
    dates = client.get("/api/video/dates").get_json()["data"]["items"]
    assert dates[0] == "2026-01-25"
    project = client.get("/api/video/project?date=2026-01-25").get_json()["data"]
    assert project["project"]["schema_version"] == "3.0"
    assert project["audit"]["planned_duration"] == 20.0
    embedded = client.get("/ranking-video/frame/open?date=2026-01-25&embed=1")
    assert embedded.status_code == 200
    assert b"__VIDEO_EMBED__=true" in embedded.data
    status = client.get("/api/mini/status").get_json()
    assert status["ok"] is True
    historical = client.get("/api/mini/status?date=2026-01-10").get_json()["data"]
    assert historical["statistic_date"] == "2026-01-10"
    assert historical["release_statistic_date"] == "2026-01-25"
    assert historical["is_historical"] is True
    assert historical["coverage"]["latest_price_count"] == 1
    assert historical["coverage"]["total_shares"] == 168
    assert historical["coverage"]["increase_count"] == 2
    assert historical["coverage"]["decrease_count"] == 0
    assert historical["coverage"]["unchanged_count"] == 0
    assert historical["coverage"]["unavailable_change_count"] == 0
    assert client.get("/api/mini/calendar?date=2026-01-10&direction=previous").get_json()["data"]["date"] == "2026-01-09"
    assert client.get("/api/mini/calendar?date=2026-01-10&direction=next").get_json()["data"]["date"] == "2026-01-11"
    assert client.get("/api/mini/calendar?date=2026-01-10&direction=invalid").status_code == 400
    ranking = client.get("/api/mini/rankings?direction=increase&pageSize=1").get_json()["data"]
    assert ranking["items"][0]["code"] == "510001"
    assert client.get("/api/mini/rankings?sort=unsafe").status_code == 400
    assert client.get("/api/mini/detail/510001?period=7").get_json()["data"]["availablePoints"] == 7
    assert client.get("/api/mini/search?keyword=ETF").status_code == 200

    added = client.post("/api/mini/watchlist/510001", json={"observation": True}).get_json()
    assert added["data"]["observation"] is True
    items = client.get("/api/mini/watchlist").get_json()["data"]["items"]
    assert len(items) == 1 and items[0]["code"] == "510001"
    assert client.delete("/api/mini/watchlist/510001").status_code == 200
    assert client.get("/api/mini/watchlist").get_json()["data"]["items"] == []
