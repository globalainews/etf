from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .calendar import TradingCalendar
from .content import build_payload
from .data import load_snapshot, refresh_scale
from .html import render_pages
from .publish import current_version, existing_is_identical, make_stage, publish_stage
from .utils import atomic_json, atomic_text, iso_now
from .validation import validate_business, validate_html_security, validate_schema
from .visual_qa import run_visual_qa


@dataclass(frozen=True)
class Settings:
    target_date: date
    publish_date: date
    db_path: Path
    output_root: Path
    archive_root: Path
    audit_root: Path
    schema_path: Path
    calendar_cache: Path
    source_root: Path
    python: Path
    skip_refresh: bool = False
    force_revision: bool = False


def _logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger(f"etf_longhubang.{log_path.parent.name}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


def build(settings: Settings, calendar: TradingCalendar | None = None) -> dict:
    target_iso = settings.target_date.isoformat()
    date_key = target_iso.replace("-", "")
    run_id = f"{date_key}_{iso_now().replace(':', '').replace('-', '')}"
    audit_dir = settings.audit_root / run_id
    audit_dir.mkdir(parents=True, exist_ok=True)
    logger = _logger(audit_dir / "producer.log")
    logger.info("start target_date=%s publish_date=%s", target_iso, settings.publish_date.isoformat())
    calendar = calendar or TradingCalendar(settings.calendar_cache)
    if not calendar.is_trading_day(settings.target_date):
        return {"status": "skipped", "reason_code": "non_trading_day", "calendar_date": target_iso, "audit_dir": str(audit_dir)}
    if not settings.skip_refresh:
        logger.info("refreshing scale data")
        refresh_scale(settings.python, settings.source_root, target_iso, audit_dir / "refresh.log")
    else:
        logger.info("scale refresh skipped by request")
    snapshot = load_snapshot(settings.db_path, target_iso)
    atomic_json(audit_dir / "source_snapshot.json", {"trade_date": snapshot.trade_date, "previous_trade_date": snapshot.previous_trade_date, **snapshot.audit})
    final_dir = settings.output_root / date_key
    version = current_version(final_dir) + 1
    generated_at = iso_now()
    payload = build_payload(snapshot, f"etf_longhubang_{date_key}_v{version}", settings.publish_date.isoformat(), generated_at)
    pages = render_pages(payload)
    schema_result = validate_schema(payload, settings.schema_path)
    business_result = validate_business(payload, pages)
    security_result = validate_html_security(pages)
    if not settings.force_revision and existing_is_identical(final_dir, payload):
        logger.info("existing package is semantically identical; publication skipped")
        report = {
            "status": "unchanged",
            "package_dir": str(final_dir),
            "package_id": json.loads((final_dir / "_READY.json").read_text(encoding="utf-8"))["package_id"],
            "audit_dir": str(audit_dir),
            "business": business_result,
            "html_security": security_result,
        }
        atomic_json(audit_dir / "qa_report.json", report)
        return report
    stage = make_stage(settings.output_root, date_key, version)
    try:
        atomic_json(stage / "etf_longhubang.json", payload)
        for filename, html in pages.items():
            atomic_text(stage / filename, html)
        visual_result = run_visual_qa(stage, audit_dir / "screenshots", [page["html_file"] for page in payload["pages"]])
        qa_report = {
            "status": "ok",
            "package_id": payload["package_id"],
            "source": snapshot.audit,
            "schema": {"status": "ok"},
            "business": business_result,
            "html_security": security_result,
            "visual": visual_result,
        }
        atomic_json(audit_dir / "qa_report.json", qa_report)
        final = publish_stage(stage, settings.output_root, settings.archive_root, payload)
        logger.info("published package=%s", final)
        return {
            "status": "ready",
            "package_id": payload["package_id"],
            "package_dir": str(final),
            "audit_dir": str(audit_dir),
        }
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)

