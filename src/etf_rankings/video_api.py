"""Local-only API and bounded job launcher for the ETF video workbench."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from typing import Any

from flask import Blueprint, current_app, jsonify, request

import db
from .video_service import VideoProjectError, available_dates, validate_project_request


video_api = Blueprint("video_api", __name__, url_prefix="/api/video")
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
_active_job: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _response(data: Any, status: int = 200):
    return jsonify({"ok": True, "data": data}), status


def _error(error: VideoProjectError):
    return jsonify({"ok": False, "error": {"code": error.code, "message": error.message}}), error.status


@video_api.errorhandler(VideoProjectError)
def handle_project_error(error: VideoProjectError):
    return _error(error)


@video_api.errorhandler(Exception)
def handle_unexpected(error: Exception):
    current_app.logger.exception("ETF video workbench API failed")
    return jsonify({"ok": False, "error": {"code": "SERVICE_ERROR", "message": "视频服务暂不可用，请稍后重试"}}), 500


def _token_valid() -> bool:
    expected = current_app.config.get("ETF_VIDEO_TOKEN", "")
    return bool(expected) and request.headers.get("X-ETF-Video-Token", "") == expected


def _write_job(job: dict[str, Any]) -> None:
    output = job.get("output")
    if not output:
        return
    path = Path(output) / "render-job.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _run_job(job_id: str, command: list[str]) -> None:
    global _active_job
    with _lock:
        job = _jobs[job_id]
        job.update({"status": "running", "started_at": _now()})
        _write_job(job)
    try:
        result = subprocess.run(command, cwd=str(Path(db.DB_PATH).parent), text=True,
                                capture_output=True, timeout=900, check=False)
        with _lock:
            job = _jobs[job_id]
            job.update({"status": "completed" if result.returncode == 0 else "failed",
                        "returncode": result.returncode, "finished_at": _now(),
                        "output_log": (result.stdout or "")[-4000:],
                        "error_log": (result.stderr or "")[-4000:]})
            _write_job(job)
    except Exception as exc:
        with _lock:
            job = _jobs[job_id]
            job.update({"status": "failed", "finished_at": _now(), "error_log": str(exc)})
            _write_job(job)
    finally:
        with _lock:
            _active_job = None


@video_api.get("/dates")
def dates():
    return _response({"items": available_dates(Path(db.DB_PATH))})


@video_api.get("/project")
def project():
    payload = {"date": request.args.get("date"), "focus_mode": request.args.get("focus_mode", "auto"),
               "focus_code": request.args.get("focus_code"), "scene_ids": ["open", "increase", "decrease", "focus", "closing"]}
    built, audit = validate_project_request(payload, Path(db.DB_PATH))
    return _response({"project": built, "audit": audit})


@video_api.post("/project/validate")
def validate():
    built, audit = validate_project_request(request.get_json(silent=True) or {}, Path(db.DB_PATH))
    return _response({"project": built, "audit": audit})


@video_api.post("/render")
def render():
    global _active_job
    if not _token_valid():
        raise VideoProjectError("FORBIDDEN", "本地视频工作台令牌无效", 403)
    payload = request.get_json(silent=True) or {}
    built, audit = validate_project_request(payload, Path(db.DB_PATH))
    if not audit["publishable"]:
        raise VideoProjectError("NO_DATA", "当前批次质量未达到视频发布条件", 422)
    with _lock:
        if _active_job:
            raise VideoProjectError("BUSY", "已有视频渲染任务正在执行", 409)
        job_id = uuid.uuid4().hex[:16]
        output = Path("output") / "etf-ranking-video" / built["trade_date"].replace("-", "")
        command = [sys.executable, "-X", "utf8", "-m", "sync.etf_sync.video_cli", "--date", built["trade_date"],
                   "--preset", "daily-20s", "--render", payload.get("render_mode", "all"),
                   "--base-url", request.host_url.rstrip("/"), "--output", str(output)]
        if payload.get("focus_mode") == "manual":
            command += ["--focus-code", str(payload["focus_code"])]
        if payload.get("scene_ids"):
            command += ["--scenes", ",".join(payload["scene_ids"])]
        job = {"id": job_id, "status": "queued", "created_at": _now(), "output": str(output),
               "trade_date": built["trade_date"], "command": command, "audit": audit}
        _jobs[job_id] = job
        _active_job = job_id
        _write_job(job)
    thread = threading.Thread(target=_run_job, args=(job_id, command), daemon=True)
    thread.start()
    return _response(job, 202)


@video_api.get("/jobs/<job_id>")
def job(job_id: str):
    with _lock:
        result = _jobs.get(job_id)
    if not result:
        raise VideoProjectError("NO_DATA", "未找到该本地渲染任务", 404)
    return _response(result)
