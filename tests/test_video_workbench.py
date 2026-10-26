from __future__ import annotations

from pathlib import Path

import pytest

from src.etf_rankings.video_service import VideoProjectError, build_video_project, validate_project_request
from sync.etf_sync.config import SyncConfig
from sync.etf_sync.local_store import materialize
from tests.test_local_materialization import make_database


def test_daily_20s_project_uses_released_data_and_dynamic_subtitles(tmp_path: Path):
    path = tmp_path / "etf.db"
    make_database(path)
    materialize(SyncConfig(db_path=path), full=True)
    project, audit = build_video_project(path, "2026-01-25")
    assert [item["id"] for item in project["scenes"]] == ["open", "increase", "decrease", "focus", "closing"]
    assert audit["planned_duration"] == 20.0
    assert project["source"]["trade_date"] == "2026-01-25"
    subtitles = [item["subtitle"] for item in project["narration"]]
    assert all(left["end"] <= right["start"] + 0.31 for left, right in zip(subtitles, subtitles[1:]))
    assert all("undefined" not in item["text"] for item in subtitles)


def test_manual_focus_is_rebuilt_from_published_rows_and_rejects_unknown_code(tmp_path: Path):
    path = tmp_path / "etf.db"
    make_database(path)
    materialize(SyncConfig(db_path=path), full=True)
    project, _ = validate_project_request(
        {"date": "2026-01-25", "focus_mode": "manual", "focus_code": "510001", "scene_ids": ["open", "focus"]}, path
    )
    assert project["focus"]["code"] == "510001"
    with pytest.raises(VideoProjectError):
        validate_project_request(
            {"date": "2026-01-25", "focus_mode": "manual", "focus_code": "000000", "scene_ids": ["open"]}, path
        )
