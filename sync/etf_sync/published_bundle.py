from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Any

from .compute import DISCLAIMER
from .config import SyncConfig


def _id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def build_published_bundle(config: SyncConfig, *, release_seq: int, initial: bool = False,
                           correction_from: str | None = None) -> dict[str, Any]:
    """Build a CloudBase upload from the atomically published local calculation batch."""
    conn = sqlite3.connect(f"file:{config.db_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        status_row = conn.execute(
            "SELECT * FROM etf_calc_publish_status WHERE id='current'"
        ).fetchone()
        if not status_row:
            raise RuntimeError("本地派生结果尚未发布，请先执行 local_cli --full")
        local = dict(status_row)
        statistic_date = local["statistic_date"]
        release_id = local["batch_id"]
        coverage = json.loads(local["coverage_json"])
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        common = {"release_id": release_id, "release_seq": release_seq,
                  "schema_version": config.schema_version}
        master = []
        for row in conn.execute("SELECT code,name,type,market,issue_year,theme FROM funds ORDER BY code"):
            item = dict(row)
            master.append({
                "_id": item["code"], "code": item["code"], "name": item["name"],
                "market": item["market"], "fund_type": item["type"],
                "sector": item["theme"] or None, "issue_year": item["issue_year"],
                "classification_source": "funds.theme" if item["theme"] else None,
                "classification_overlaps": False,
                "search_keys": _search_keys(item["code"], item["name"]),
                "updated_at": now, "schema_version": config.schema_version,
            })
        latest = [dict(row) for row in conn.execute(
            "SELECT * FROM etf_derived_daily WHERE date=? ORDER BY code", (statistic_date,)
        )]
        if not latest:
            raise RuntimeError("本地发布指针对应的榜单为空")
        ranking, streaks, anomalies = [], [], []
        for row in latest:
            base = {"_id": _id(release_id, row["code"]), **common,
                    "statistic_date": row["date"], "baseline_date": row["baseline_date"],
                    "code": row["code"], "name": row["name"], "market": row["market"],
                    "sector": row["sector"]}
            ranking.append({**base, "shares": row["shares"], "baseline_shares": row["baseline_shares"],
                            "delta_shares": row["delta_shares"], "change_rate": row["change_rate"],
                            "change_status": row["change_status"], "price_close": row["price_close"],
                            "baseline_price_close": row["baseline_price_close"],
                            "price_change_rate": row["price_change_rate"], "price_status": row["price_status"],
                            "direction": row["direction"], "rank": row["delta_rank"],
                            "rate_rank": row["rate_rank"], "special_event": row["special_event"],
                            "special_event_status": row["special_event_status"],
                            "quality_status": row["quality_status"], "source": "local_sqlite_published",
                            "synced_at": now})
            streaks.append({**base, "direction": row["streak_direction"], "days": row["streak_days"],
                            "start_date": row["streak_start_date"], "end_date": row["date"],
                            "cumulative_change_rate": row["streak_change_rate"],
                            "status": row["streak_status"], "rank": row["delta_rank"]})
            anomalies.append({**base, "change_rate": row["change_rate"],
                              "history_median_abs_rate": row["anomaly_median_abs_rate"],
                              "anomaly_ratio": row["anomaly_ratio"],
                              "price_change_rate": row["price_change_rate"],
                              "status": row["anomaly_status"],
                              "special_event": row["special_event"],
                              "qualified": bool(row["anomaly_qualified"]),
                              "rank": row["rate_rank"],
                              "rule": {"window": config.anomaly_window,
                                       "min_history": config.anomaly_min_history,
                                       "ratio_threshold": config.anomaly_ratio_threshold,
                                       "abs_pct_threshold": config.anomaly_abs_pct_threshold,
                                       "excludes_current": True}})
        sectors = []
        for row in conn.execute("SELECT * FROM etf_sector_daily WHERE date=? ORDER BY rank,sector", (statistic_date,)):
            item = dict(row)
            sectors.append({"_id": _id(release_id, item["sector"]), **common,
                            "statistic_date": item["date"], "sector": item["sector"],
                            "median_change_rate": item["median_change_rate"],
                            "valid_sample_count": item["valid_sample_count"],
                            "missing_sample_count": item["missing_sample_count"],
                            "sample_count": item["sample_count"], "rank": item["rank"],
                            "classification_source": item["classification_source"],
                            "classification_overlaps": bool(item["classification_overlaps"])})
        dates = [row[0] for row in conn.execute("SELECT DISTINCT date FROM fund_scale ORDER BY date")]
        if correction_from:
            history_dates = [day for day in dates if correction_from <= day <= statistic_date]
        elif initial:
            history_dates = dates[-config.history_days:]
        else:
            history_dates = [statistic_date]
        history = _history(conn, history_dates, common, release_seq, now)
        release = {
            "_id": "current", **common, "statistic_date": statistic_date,
            "baseline_date": local["baseline_date"], "updated_at": local["updated_at"],
            "coverage": coverage, "quality_status": local["quality_status"],
            "disclaimer": DISCLAIMER, "share_unit": "份", "display_share_unit": "亿份",
            "price_currency": "CNY", "price_adjustment": "前复权",
            "source": "etf.db/etf_calc_publish_status",
            "master_hash": _hash([{key: value for key, value in row.items()
                                    if key != "updated_at"} for row in master]),
            "date_basis": "fund_scale.date (source statistic date, never collection clock)",
            "disclosure_date_verification": "SOURCE_DATE_ONLY",
            "known_limitations": ["no corporate-action adjustment source",
                                  "cross-border disclosure lag not independently modeled"],
        }
        collections = {"etf_master": master, "etf_history": history,
                       "ranking_snapshots": ranking, "streak_snapshots": streaks,
                       "anomaly_snapshots": anomalies, "sector_snapshots": sectors}
        batch = {"_id": release_id, **common, "state": "prepared", "created_at": now,
                 "statistic_date": statistic_date, "baseline_date": local["baseline_date"],
                 "local_batch_id": release_id,
                 "counts": {name: len(items) for name, items in collections.items()}}
        batch["payload_hash"] = _hash({name: [_hash(row) for row in rows]
                                       for name, rows in collections.items()})
        config_payload = asdict(config)
        config_payload["db_path"] = str(config.db_path)
        config_payload["api_key"] = ""
        return {"release": release, "batch": batch, "collections": collections,
                "config": config_payload}
    finally:
        conn.close()


def _history(conn: sqlite3.Connection, dates: list[str], common: dict[str, Any],
             release_seq: int, now: str) -> list[dict[str, Any]]:
    if not dates:
        return []
    placeholders = ",".join("?" for _ in dates)
    scale = {(row["date"], row["code"]): row["shares"] for row in conn.execute(
        f"SELECT date,code,shares FROM fund_scale WHERE date IN ({placeholders})", dates
    )}
    price = {(row["date"], row["code"]): row["close"] for row in conn.execute(
        f"SELECT date,code,close FROM fund_price WHERE date IN ({placeholders})", dates
    )}
    return [{"_id": _id(code, date, str(release_seq)), **common, "date": date, "code": code,
             "shares": scale.get((date, code)), "close": price.get((date, code)),
             "share_status": "OK" if (date, code) in scale else "MISSING",
             "price_status": "OK" if (date, code) in price else "MISSING",
             "source": "local_sqlite", "synced_at": now}
            for date, code in sorted(set(scale) | set(price))]


def _search_keys(code: str, name: str) -> list[str]:
    compact = "".join(name.lower().split())
    keys = {code, compact}
    keys.update(code[:size] for size in range(1, len(code) + 1))
    keys.update(compact[:size] for size in range(1, min(len(compact), 24) + 1))
    return sorted(keys)
