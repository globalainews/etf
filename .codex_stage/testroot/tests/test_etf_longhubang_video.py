from __future__ import annotations

import json
from pathlib import Path

import pytest

from etf_longhubang.html import render_pages
from etf_longhubang.utils import atomic_json, atomic_text, sha256_file
from etf_longhubang.video import (
    VideoBuildError,
    VideoContext,
    normalize_text,
    split_spoken_chunks,
    stage_narration,
    stage_prepare,
    subtitle_cards,
)
from etf_longhubang.video_cli import resolve_date


ROOT = Path(__file__).resolve().parents[1]


def make_package(root: Path) -> tuple[Path, dict]:
    payload = json.loads(
        (ROOT / "docs" / "examples" / "etf_longhubang" / "etf_longhubang.json").read_text(encoding="utf-8")
    )
    package = root / "input" / "20261023"
    package.mkdir(parents=True)
    atomic_json(package / "etf_longhubang.json", payload)
    for name, html in render_pages(payload).items():
        atomic_text(package / name, html)
    atomic_json(
        package / "_READY.json",
        {
            "schema_version": "1.0",
            "content_type": "etf_longhubang",
            "package_id": payload["package_id"],
            "trade_date": payload["trade_date"],
            "created_at": "2026-10-24T06:05:10+08:00",
            "main_file": "etf_longhubang.json",
            "main_file_sha256": sha256_file(package / "etf_longhubang.json"),
            "file_count": 5,
        },
    )
    return package, payload


def context(tmp_path: Path) -> VideoContext:
    return VideoContext(
        "20261023",
        input_root=tmp_path / "input",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
        schema_path=ROOT / "docs" / "schemas" / "etf_longhubang.v1.schema.json",
    )


def test_prepare_accepts_complete_ready_package(tmp_path: Path) -> None:
    package, payload = make_package(tmp_path)
    report = json.loads(stage_prepare(context(tmp_path)).read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    assert report["package_id"] == payload["package_id"]
    assert report["main_file_sha256"] == sha256_file(package / "etf_longhubang.json")


def test_prepare_rejects_tampered_main_file(tmp_path: Path) -> None:
    package, payload = make_package(tmp_path)
    payload["title"] = "已篡改"
    atomic_json(package / "etf_longhubang.json", payload)
    with pytest.raises(VideoBuildError, match="SHA-256"):
        stage_prepare(context(tmp_path))


def test_spoken_chunks_align_with_all_four_subtitle_plans(tmp_path: Path) -> None:
    _, payload = make_package(tmp_path)
    for page in payload["pages"]:
        chunks = split_spoken_chunks(page["tts_text"], len(page["subtitle_sentences"]))
        assert len(chunks) == len(page["subtitle_sentences"])
        assert normalize_text("".join(chunks)) == normalize_text(page["tts_text"])


def test_narration_keeps_authoritative_text_and_one_chunk_per_subtitle(tmp_path: Path) -> None:
    _, payload = make_package(tmp_path)
    ctx = context(tmp_path)
    output = json.loads(stage_narration(ctx).read_text(encoding="utf-8"))
    for source, page in zip(payload["pages"], output["pages"]):
        assert page["text"] == source["tts_text"]
        assert len(page["chunks"]) == len(source["subtitle_sentences"])
        assert [row["subtitle_text"] for row in page["chunks"]] == source["subtitle_sentences"]


def test_subtitle_cards_never_exceed_two_lines_or_sixteen_chars() -> None:
    cards = subtitle_cards("第1名半导体设备ETF国泰，减少15.75亿份，这是今天减少榜的第一名。")
    assert cards
    for card in cards:
        lines = card.splitlines()
        assert len(lines) <= 2
        assert all(len(line) <= 16 for line in lines)


def test_default_date_selects_latest_ready_package(tmp_path: Path) -> None:
    for value, ready in (("20261022", True), ("20261023", True), ("20261024", False)):
        directory = tmp_path / value
        directory.mkdir()
        if ready:
            atomic_json(directory / "_READY.json", {"ok": True})
    assert resolve_date(None, tmp_path) == "20261023"
