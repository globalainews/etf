from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path

from . import CONTENT_TYPE, SCHEMA_VERSION
from .errors import PublicationError
from .utils import atomic_json, iso_now, sha256_file


VERSION_RE = re.compile(r"_v([1-9][0-9]*)$")


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def current_version(final_dir: Path) -> int:
    ready = _read_json(final_dir / "_READY.json") or {}
    match = VERSION_RE.search(str(ready.get("package_id", "")))
    return int(match.group(1)) if match else 0


def canonical_payload(payload: dict) -> dict:
    clone = json.loads(json.dumps(payload, ensure_ascii=False))
    clone.pop("package_id", None)
    clone.pop("generated_at", None)
    if isinstance(clone.get("source"), dict):
        clone["source"].pop("as_of", None)
    return clone


def existing_is_identical(final_dir: Path, candidate: dict) -> bool:
    ready = _read_json(final_dir / "_READY.json")
    existing = _read_json(final_dir / "etf_longhubang.json")
    if not ready or not existing:
        return False
    main = final_dir / "etf_longhubang.json"
    if ready.get("main_file_sha256") != sha256_file(main):
        return False
    return canonical_payload(existing) == canonical_payload(candidate)


def make_stage(output_root: Path, date_key: str, version: int) -> Path:
    stage = output_root / f".{date_key}.v{version}.{uuid.uuid4().hex[:8]}.tmp"
    stage.mkdir(parents=True, exist_ok=False)
    return stage


def publish_stage(stage: Path, output_root: Path, archive_root: Path, payload: dict) -> Path:
    date_key = payload["trade_date"].replace("-", "")
    final = output_root / date_key
    archived: Path | None = None
    try:
        if final.exists():
            existing_ready = _read_json(final / "_READY.json") or {}
            archive_name = existing_ready.get("package_id") or f"unknown_{iso_now().replace(':', '')}"
            archived = archive_root / date_key / str(archive_name)
            if archived.exists():
                archived = archived.with_name(f"{archived.name}_{uuid.uuid4().hex[:6]}")
            archived.parent.mkdir(parents=True, exist_ok=True)
            os.replace(final, archived)
        os.replace(stage, final)
        main = final / "etf_longhubang.json"
        ready = {
            "schema_version": SCHEMA_VERSION,
            "content_type": CONTENT_TYPE,
            "package_id": payload["package_id"],
            "trade_date": payload["trade_date"],
            "created_at": iso_now(),
            "main_file": "etf_longhubang.json",
            "main_file_sha256": sha256_file(main),
            "file_count": 5,
        }
        atomic_json(final / "_READY.json", ready)
        return final
    except Exception as exc:
        try:
            failed = output_root / f".{date_key}.failed.{uuid.uuid4().hex[:6]}"
            if final.exists() and not (final / "_READY.json").exists():
                os.replace(final, failed)
            if archived and archived.exists() and not final.exists():
                os.replace(archived, final)
        except OSError:
            pass
        raise PublicationError(f"发布素材包失败：{exc}") from exc


def write_skip(output_root: Path, archive_root: Path, calendar_date: str, reason_code: str, reason: str) -> Path | None:
    date_key = calendar_date.replace("-", "")
    final = output_root / date_key
    if (final / "_READY.json").exists():
        return None
    stage = output_root / f".{date_key}.skip.{uuid.uuid4().hex[:8]}.tmp"
    stage.mkdir(parents=True, exist_ok=False)
    atomic_json(
        stage / "_SKIP.json",
        {
            "schema_version": SCHEMA_VERSION,
            "content_type": CONTENT_TYPE,
            "calendar_date": calendar_date,
            "status": "skipped",
            "reason_code": reason_code,
            "reason": reason,
        },
    )
    if final.exists():
        old = archive_root / date_key / f"skip_{uuid.uuid4().hex[:8]}"
        old.parent.mkdir(parents=True, exist_ok=True)
        os.replace(final, old)
    os.replace(stage, final)
    return final

