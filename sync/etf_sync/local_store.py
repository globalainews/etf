from __future__ import annotations

from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import statistics
import time
from typing import Any, Iterator

import db

from .compute import anomaly_ratio, change_rate, stable_rank
from .config import SyncConfig


CALC_VERSION = "2.0"
LOCAL_USER_ID = "local-default"
DERIVED_COLUMNS = (
    "date", "code", "name", "market", "sector", "baseline_date", "shares",
    "baseline_shares", "delta_shares", "change_rate", "change_status", "price_close",
    "baseline_price_close", "price_change_rate", "price_status", "direction", "delta_rank",
    "rate_rank", "streak_direction", "streak_days", "streak_start_date", "streak_change_rate",
    "streak_status", "anomaly_median_abs_rate", "anomaly_ratio", "anomaly_status",
    "anomaly_qualified", "special_event", "special_event_status", "quality_status",
    "calc_version", "batch_id",
)
SECTOR_COLUMNS = (
    "date", "sector", "median_change_rate", "valid_sample_count", "missing_sample_count",
    "sample_count", "rank", "classification_source", "classification_overlaps",
    "calc_version", "batch_id",
)


@dataclass
class BuildSummary:
    batch_id: str
    mode: str
    requested_from: str | None
    effective_from: str
    statistic_date: str
    baseline_date: str | None
    derived_count: int
    sector_count: int
    dry_run: bool
    published: bool

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _signature(conn: sqlite3.Connection) -> dict[str, Any]:
    scale = conn.execute(
        "SELECT COUNT(*),MIN(date),MAX(date),ROUND(SUM(shares),4) FROM fund_scale"
    ).fetchone()
    price = conn.execute(
        "SELECT COUNT(*),MIN(date),MAX(date),ROUND(SUM(close),4) FROM fund_price"
    ).fetchone()
    return {"scale": list(scale), "price": list(price)}


def _batch_id(signature: dict[str, Any], mode: str, requested_from: str | None) -> str:
    raw = json.dumps([signature, mode, requested_from, time.time_ns()], ensure_ascii=False).encode("utf-8")
    return "local-" + hashlib.sha256(raw).hexdigest()[:16]


@contextmanager
def calculation_lock(db_path: Path) -> Iterator[None]:
    lock_path = db_path.with_suffix(db_path.suffix + ".etf-calc.lock")
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"已有本地计算任务在运行：{lock_path}") from exc
    try:
        os.write(fd, f"pid={os.getpid()} started={_utcnow()}".encode("utf-8"))
        os.close(fd)
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _validate_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("日期格式须为 YYYY-MM-DD") from exc


def _rank(rows: list[dict[str, Any]], direction: str, field: str, target: str) -> None:
    selected = [row for row in rows if row["direction"] == direction and row[field] is not None]
    stable_rank(selected, field, descending=direction == "increase")
    for row in selected:
        row[target] = row.pop("rank")


def _row_values(row: dict[str, Any], columns: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(row.get(column) for column in columns)


def _prepare_range(conn: sqlite3.Connection, *, full: bool, incremental: bool,
                   correction_from: str | None) -> tuple[str, str | None, list[str]]:
    dates = [row[0] for row in conn.execute("SELECT DISTINCT date FROM fund_scale ORDER BY date")]
    if len(dates) < 2:
        raise ValueError("fund_scale 至少需要两个统计日")
    correction_from = _validate_date(correction_from)
    status = conn.execute(
        "SELECT statistic_date FROM etf_calc_publish_status WHERE id='current'"
    ).fetchone()
    if full:
        requested = dates[0]
    elif correction_from:
        if correction_from > dates[-1]:
            raise ValueError("修正起始日晚于最新数据日")
        requested = next((day for day in dates if day >= correction_from), dates[-1])
    elif incremental:
        if not status:
            raise ValueError("尚无本地发布批次，请先执行 --full")
        requested = next((day for day in dates if day > status[0]), dates[-1])
    else:
        raise ValueError("需要指定 --full、--incremental 或 --correction-from")
    return requested, dates[-2], dates


def materialize(config: SyncConfig, *, full: bool = False, incremental: bool = False,
                correction_from: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    db_path = config.db_path.resolve()
    with calculation_lock(db_path):
        conn = db.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            requested_from, baseline_date, dates = _prepare_range(
                conn, full=full, incremental=incremental, correction_from=correction_from
            )
            mode = "full" if full else "correction" if correction_from else "incremental"
            signature = _signature(conn)
            batch_id = _batch_id(signature, mode, requested_from)
            if dry_run:
                count = conn.execute(
                    "SELECT COUNT(*) FROM fund_scale WHERE date>=?", (requested_from,)
                ).fetchone()[0]
                sectors = conn.execute(
                    """SELECT COUNT(*) FROM (
                       SELECT DISTINCT s.date,f.theme FROM fund_scale s JOIN funds f ON f.code=s.code
                       WHERE s.date>=? AND LENGTH(f.theme)>0)""", (requested_from,)
                ).fetchone()[0]
                return BuildSummary(batch_id, mode, requested_from, requested_from, dates[-1],
                                    baseline_date, count, sectors, True, False).as_dict()
            return _build_and_publish(conn, config, dates, requested_from, batch_id, mode,
                                      signature, baseline_date)
        finally:
            conn.close()


def _build_and_publish(conn: sqlite3.Connection, config: SyncConfig, dates: list[str],
                       requested_from: str, batch_id: str, mode: str,
                       signature: dict[str, Any], baseline_date: str | None) -> dict[str, Any]:
    now = _utcnow()
    config_json = json.dumps({
        "anomaly_window": config.anomaly_window,
        "anomaly_min_history": config.anomaly_min_history,
        "anomaly_ratio_threshold": config.anomaly_ratio_threshold,
        "anomaly_abs_pct_threshold": config.anomaly_abs_pct_threshold,
        "calc_version": CALC_VERSION,
    }, ensure_ascii=False, sort_keys=True)
    conn.execute("DELETE FROM etf_derived_daily_stage")
    conn.execute("DELETE FROM etf_sector_daily_stage")
    conn.execute(
        """INSERT INTO etf_calc_batches(
           batch_id,state,mode,requested_from,effective_from,statistic_date,baseline_date,
           source_signature,config_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (batch_id, "calculating", mode, requested_from, requested_from, dates[-1], baseline_date,
         json.dumps(signature, ensure_ascii=False, sort_keys=True), config_json, now),
    )
    conn.commit()
    funds = {row["code"]: dict(row) for row in conn.execute(
        "SELECT code,name,market,theme,type FROM funds ORDER BY code"
    )}
    anomaly_history: dict[str, deque[float]] = defaultdict(
        lambda: deque(maxlen=config.anomaly_window)
    )
    streak_state: dict[str, dict[str, Any]] = {}
    previous: dict[str, tuple[float, float | None]] = {}
    derived_count = sector_count = 0
    derived_sql = (
        f"INSERT INTO etf_derived_daily_stage({','.join(DERIVED_COLUMNS)}) "
        f"VALUES({','.join('?' for _ in DERIVED_COLUMNS)})"
    )
    sector_sql = (
        f"INSERT INTO etf_sector_daily_stage({','.join(SECTOR_COLUMNS)}) "
        f"VALUES({','.join('?' for _ in SECTOR_COLUMNS)})"
    )
    try:
        for date_index, date in enumerate(dates):
            current_rows = list(conn.execute(
                """SELECT s.code,s.shares,p.close FROM fund_scale s
                   LEFT JOIN fund_price p ON p.date=s.date AND p.code=s.code
                   WHERE s.date=? ORDER BY s.code""", (date,)
            ))
            current = {row["code"]: (float(row["shares"]), row["close"]) for row in current_rows}
            daily: list[dict[str, Any]] = []
            sector_rates: dict[str, list[float]] = defaultdict(list)
            sector_samples: dict[str, int] = defaultdict(int)
            previous_date = dates[date_index - 1] if date_index else None
            for raw in current_rows:
                code = raw["code"]
                shares = float(raw["shares"])
                close = float(raw["close"]) if raw["close"] is not None else None
                old = previous.get(code)
                old_shares = old[0] if old else None
                old_close = old[1] if old else None
                rate, rate_status = change_rate(shares, old_shares)
                price_rate, price_status = change_rate(close, old_close)
                delta = shares - old_shares if old_shares is not None else None
                direction = "increase" if delta is not None and delta > 0 else "decrease" if delta is not None and delta < 0 else "flat"
                prior = list(anomaly_history[code])
                ratio, anomaly_status = anomaly_ratio(rate, prior, config.anomaly_min_history)
                median_abs = statistics.median(abs(item) for item in prior[-config.anomaly_window:]) if len(prior) >= config.anomaly_min_history else None
                qualified = int(
                    ratio is not None and ratio >= config.anomaly_ratio_threshold and
                    abs(rate or 0) >= config.anomaly_abs_pct_threshold / 100
                )
                prior_streak = streak_state.get(code)
                if direction in {"increase", "decrease"} and old_shares is not None:
                    if prior_streak and prior_streak["last_index"] == date_index - 1 and prior_streak["direction"] == direction:
                        streak_days = prior_streak["days"] + 1
                        streak_start_date = prior_streak["start_date"]
                        streak_base = prior_streak["base_shares"]
                    else:
                        streak_days = 1
                        streak_start_date = previous_date
                        streak_base = old_shares
                    streak_rate, _ = change_rate(shares, streak_base)
                    streak_state[code] = {"direction": direction, "days": streak_days,
                                          "start_date": streak_start_date, "base_shares": streak_base,
                                          "last_index": date_index}
                    streak_status = "OK"
                else:
                    streak_days, streak_start_date, streak_rate = 0, None, None
                    streak_state[code] = {"direction": None, "days": 0, "start_date": None,
                                          "base_shares": shares, "last_index": date_index}
                    streak_status = "UNCHANGED" if delta == 0 else "DATA_INSUFFICIENT"
                fund = funds.get(code, {"name": code, "market": "", "theme": "", "type": ""})
                sector = fund.get("theme") or None
                if sector:
                    sector_samples[sector] += 1
                    if rate is not None:
                        sector_rates[sector].append(rate)
                daily.append({
                    "date": date, "code": code, "name": fund.get("name") or code,
                    "market": fund.get("market") or "", "sector": sector,
                    "baseline_date": previous_date, "shares": shares,
                    "baseline_shares": old_shares, "delta_shares": delta,
                    "change_rate": rate, "change_status": rate_status,
                    "price_close": close, "baseline_price_close": old_close,
                    "price_change_rate": price_rate, "price_status": price_status,
                    "direction": direction, "delta_rank": None, "rate_rank": None,
                    "streak_direction": direction if streak_days else None,
                    "streak_days": streak_days, "streak_start_date": streak_start_date,
                    "streak_change_rate": streak_rate, "streak_status": streak_status,
                    "anomaly_median_abs_rate": median_abs, "anomaly_ratio": ratio,
                    "anomaly_status": anomaly_status, "anomaly_qualified": qualified,
                    "special_event": None, "special_event_status": "NO_RELIABLE_SOURCE",
                    "quality_status": "OK" if rate_status == "OK" else "PARTIAL",
                    "calc_version": CALC_VERSION, "batch_id": batch_id,
                })
                if rate is not None:
                    anomaly_history[code].append(rate)
            for direction in ("increase", "decrease"):
                _rank(daily, direction, "delta_shares", "delta_rank")
                _rank(daily, direction, "change_rate", "rate_rank")
            sectors = []
            for sector in sorted(sector_samples):
                rates = sector_rates[sector]
                sectors.append({
                    "date": date, "sector": sector,
                    "median_change_rate": statistics.median(rates) if rates else None,
                    "valid_sample_count": len(rates),
                    "missing_sample_count": sector_samples[sector] - len(rates),
                    "sample_count": sector_samples[sector], "rank": None,
                    "classification_source": "funds.theme", "classification_overlaps": 0,
                    "calc_version": CALC_VERSION, "batch_id": batch_id,
                })
            stable_rank([row for row in sectors if row["median_change_rate"] is not None],
                        "median_change_rate", True)
            for row in sectors:
                if "rank" not in row or row["rank"] is None:
                    row["rank"] = row.pop("rank", None)
            if date >= requested_from:
                conn.executemany(derived_sql, [_row_values(row, DERIVED_COLUMNS) for row in daily])
                conn.executemany(sector_sql, [_row_values(row, SECTOR_COLUMNS) for row in sectors])
                derived_count += len(daily)
                sector_count += len(sectors)
                if date_index % 20 == 0:
                    conn.commit()
            previous = current
        conn.commit()
        _publish(conn, batch_id, requested_from, dates[-1], baseline_date,
                 derived_count, sector_count, funds)
        return BuildSummary(batch_id, mode, requested_from, requested_from, dates[-1],
                            baseline_date, derived_count, sector_count, False, True).as_dict()
    except Exception as exc:
        conn.rollback()
        conn.execute(
            "UPDATE etf_calc_batches SET state='failed',completed_at=?,error_type=?,error_message=? WHERE batch_id=?",
            (_utcnow(), type(exc).__name__, str(exc)[:500], batch_id),
        )
        conn.commit()
        raise


def _publish(conn: sqlite3.Connection, batch_id: str, effective_from: str,
             statistic_date: str, baseline_date: str | None, derived_count: int,
             sector_count: int, funds: dict[str, dict[str, Any]]) -> None:
    staged = conn.execute("SELECT COUNT(*) FROM etf_derived_daily_stage").fetchone()[0]
    if staged != derived_count or not staged:
        raise ValueError(f"派生结果校验失败：expected={derived_count}, staged={staged}")
    duplicate = conn.execute(
        """SELECT 1 FROM etf_derived_daily_stage GROUP BY date,code HAVING COUNT(*)>1 LIMIT 1"""
    ).fetchone()
    if duplicate:
        raise ValueError("派生结果存在重复业务键")
    latest_count = conn.execute(
        "SELECT COUNT(*) FROM etf_derived_daily_stage WHERE date=?", (statistic_date,)
    ).fetchone()[0]
    if not latest_count:
        raise ValueError("暂存区缺少最新统计日")
    coverage = {
        "fund_total": len(funds),
        "latest_share_count": latest_count,
        "comparable_count": conn.execute(
            "SELECT COUNT(*) FROM etf_derived_daily_stage WHERE date=? AND change_status='OK'",
            (statistic_date,),
        ).fetchone()[0],
        "latest_price_count": conn.execute(
            "SELECT COUNT(DISTINCT code) FROM fund_price WHERE date=? AND close IS NOT NULL",
            (statistic_date,),
        ).fetchone()[0],
        "classification_count": sum(bool(row.get("theme")) for row in funds.values()),
    }
    completed = _utcnow()
    with conn:
        conn.execute("DELETE FROM etf_derived_daily WHERE date>=?", (effective_from,))
        conn.execute("DELETE FROM etf_sector_daily WHERE date>=?", (effective_from,))
        conn.execute("INSERT INTO etf_derived_daily SELECT * FROM etf_derived_daily_stage")
        conn.execute("INSERT INTO etf_sector_daily SELECT * FROM etf_sector_daily_stage")
        conn.execute(
            """INSERT INTO etf_calc_publish_status(
               id,batch_id,statistic_date,baseline_date,effective_from,updated_at,
               quality_status,coverage_json,calc_version) VALUES('current',?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET batch_id=excluded.batch_id,
               statistic_date=excluded.statistic_date,baseline_date=excluded.baseline_date,
               effective_from=excluded.effective_from,updated_at=excluded.updated_at,
               quality_status=excluded.quality_status,coverage_json=excluded.coverage_json,
               calc_version=excluded.calc_version""",
            (batch_id, statistic_date, baseline_date, effective_from, completed,
             "COMPLETE_FOR_COVERED_SAMPLE", json.dumps(coverage, ensure_ascii=False), CALC_VERSION),
        )
        conn.execute(
            """UPDATE etf_calc_batches SET state='published',completed_at=?,derived_count=?,
               sector_count=? WHERE batch_id=?""",
            (completed, derived_count, sector_count, batch_id),
        )
        conn.execute("DELETE FROM etf_derived_daily_stage")
        conn.execute("DELETE FROM etf_sector_daily_stage")


def verify(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        status = conn.execute(
            "SELECT * FROM etf_calc_publish_status WHERE id='current'"
        ).fetchone()
        if not status:
            return {"ok": False, "error": "NO_PUBLISHED_BATCH"}
        date = status["statistic_date"]
        derived = conn.execute(
            "SELECT COUNT(*) FROM etf_derived_daily WHERE date=?", (date,)
        ).fetchone()[0]
        raw = conn.execute("SELECT COUNT(*) FROM fund_scale WHERE date=?", (date,)).fetchone()[0]
        orphan = conn.execute(
            """SELECT COUNT(*) FROM etf_derived_daily d LEFT JOIN funds f ON f.code=d.code
               WHERE d.date=? AND f.code IS NULL""", (date,)
        ).fetchone()[0]
        return {"ok": derived == raw and orphan == 0, "batch_id": status["batch_id"],
                "statistic_date": date, "derived_count": derived, "raw_count": raw,
                "orphan_count": orphan, "quality_status": status["quality_status"]}
    finally:
        conn.close()
