from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
from typing import Any

from .config import SyncConfig


DISCLAIMER = "份额变化不代表未来涨跌，不等同资金净流入。"


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def change_rate(current: float | None, baseline: float | None) -> tuple[float | None, str]:
    current, baseline = _finite(current), _finite(baseline)
    if current is None:
        return None, "CURRENT_MISSING"
    if baseline is None:
        return None, "BASELINE_MISSING"
    if baseline <= 0:
        return None, "BASELINE_INVALID"
    return (current - baseline) / baseline, "OK"


def stable_rank(rows: list[dict], field: str, descending: bool = True) -> list[dict]:
    def key(row: dict) -> tuple:
        value = row.get(field)
        missing = value is None
        numeric = 0 if missing else float(value)
        return (missing, -numeric if descending else numeric, row.get("code", ""))

    ranked = sorted(rows, key=key)
    previous = object()
    display_rank = 0
    for index, row in enumerate(ranked, 1):
        value = row.get(field)
        if value != previous:
            display_rank = index
            previous = value
        row["rank"] = display_rank
    return ranked


def anomaly_ratio(current_rate: float | None, prior_rates: list[float | None], min_history: int = 20) -> tuple[float | None, str]:
    valid = [abs(float(value)) for value in prior_rates if value is not None and math.isfinite(float(value))]
    if current_rate is None:
        return None, "CURRENT_UNAVAILABLE"
    if len(valid) < min_history:
        return None, "HISTORY_INSUFFICIENT"
    baseline = statistics.median(valid[-min_history:])
    if baseline == 0:
        return None, "BASELINE_ZERO"
    return abs(current_rate) / baseline, "OK"


def _read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _doc_id(*parts: str) -> str:
    return "_".join(str(part).replace("/", "_") for part in parts)


def _search_keys(code: str, name: str) -> list[str]:
    values = {code[:i] for i in range(1, len(code) + 1)}
    normalized = "".join(name.lower().split())
    values.update(normalized[:i] for i in range(1, min(len(normalized), 24) + 1))
    return sorted(value for value in values if value)


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _streak(code: str, dates: list[str], shares: dict[str, dict[str, float]]) -> dict:
    series = shares.get(code, {})
    if len(dates) < 2 or dates[-1] not in series:
        return {"direction": None, "days": 0, "status": "DATA_INSUFFICIENT"}
    direction = None
    days = 0
    end_index = len(dates) - 1
    start_index = end_index
    for index in range(end_index, 0, -1):
        today, yesterday = dates[index], dates[index - 1]
        if today not in series or yesterday not in series:
            break
        delta = series[today] - series[yesterday]
        sign = "increase" if delta > 0 else "decrease" if delta < 0 else None
        if sign is None:
            break
        if direction is None:
            direction = sign
        if sign != direction:
            break
        days += 1
        start_index = index - 1
    if not direction:
        return {"direction": None, "days": 0, "status": "UNCHANGED"}
    start_value, end_value = series[dates[start_index]], series[dates[end_index]]
    rate, status = change_rate(end_value, start_value)
    return {
        "direction": direction,
        "days": days,
        "start_date": dates[start_index],
        "end_date": dates[end_index],
        "cumulative_delta_shares": end_value - start_value,
        "cumulative_change_rate": rate,
        "status": status,
    }


def build_release_bundle(config: SyncConfig, *, release_id: str | None = None, release_seq: int = 1,
                         initial: bool = False, correction_from: str | None = None) -> dict[str, Any]:
    if not config.db_path.exists():
        raise FileNotFoundError(config.db_path)
    conn = _read_only(config.db_path)
    try:
        all_dates = [row[0] for row in conn.execute("SELECT DISTINCT date FROM fund_scale ORDER BY date")]
        if len(all_dates) < 2:
            raise ValueError("fund_scale 至少需要两个有效统计日")
        statistic_date, baseline_date = all_dates[-1], all_dates[-2]
        lookback = max(config.history_days + 2, config.anomaly_window + 3)
        dates = all_dates[-lookback:]
        placeholders = ",".join("?" for _ in dates)
        funds = {row["code"]: dict(row) for row in conn.execute(
            "SELECT code,name,type,market,issue_year,theme FROM funds ORDER BY code"
        )}
        shares: dict[str, dict[str, float]] = defaultdict(dict)
        prices: dict[str, dict[str, float]] = defaultdict(dict)
        for row in conn.execute(
            f"SELECT date,code,shares FROM fund_scale WHERE date IN ({placeholders}) ORDER BY code,date", dates
        ):
            shares[row["code"]][row["date"]] = float(row["shares"])
        for row in conn.execute(
            f"SELECT date,code,close FROM fund_price WHERE date IN ({placeholders}) ORDER BY code,date", dates
        ):
            prices[row["code"]][row["date"]] = float(row["close"])
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db_stat = config.db_path.stat()
        wal_path = config.db_path.with_name(config.db_path.name + "-wal")
        wal_stat = wal_path.stat() if wal_path.exists() else None
        source_signature = {
            "statistic_date": statistic_date,
            "baseline_date": baseline_date,
            "latest_count": sum(statistic_date in values for values in shares.values()),
            "latest_sum": sum(values.get(statistic_date, 0) for values in shares.values()),
            "db_mtime_ns": db_stat.st_mtime_ns,
            "db_size": db_stat.st_size,
            "wal_mtime_ns": wal_stat.st_mtime_ns if wal_stat else 0,
            "wal_size": wal_stat.st_size if wal_stat else 0,
        }
        release_id = release_id or f"r{statistic_date.replace('-', '')}-{_hash_payload(source_signature)[:10]}"
        common = {"release_id": release_id, "release_seq": release_seq, "schema_version": config.schema_version}

        masters = []
        for code, fund in funds.items():
            masters.append({
                "_id": code,
                "code": code,
                "name": fund.get("name") or code,
                "market": fund.get("market") or "",
                "fund_type": fund.get("type") or "",
                "sector": fund.get("theme") or None,
                "issue_year": fund.get("issue_year"),
                "classification_source": "funds.theme" if fund.get("theme") else None,
                "classification_overlaps": False,
                "search_keys": _search_keys(code, fund.get("name") or code),
                "updated_at": now,
                "schema_version": config.schema_version,
            })

        latest_codes = {code for code, values in shares.items() if statistic_date in values}
        comparable_codes = {code for code in latest_codes if baseline_date in shares[code]}
        ranking_rows = []
        anomaly_rows = []
        streak_rows = []
        daily_rates_by_sector: dict[str, list[float]] = defaultdict(list)
        sector_latest_codes: dict[str, set[str]] = defaultdict(set)

        for code in sorted(latest_codes):
            fund = funds.get(code, {"name": code, "market": "", "theme": ""})
            current = shares[code].get(statistic_date)
            baseline = shares[code].get(baseline_date)
            rate, rate_status = change_rate(current, baseline)
            delta = current - baseline if current is not None and baseline is not None else None
            current_price, baseline_price = prices[code].get(statistic_date), prices[code].get(baseline_date)
            price_rate, price_status = change_rate(current_price, baseline_price)
            row = {
                "_id": _doc_id(release_id, code), **common,
                "statistic_date": statistic_date,
                "baseline_date": baseline_date,
                "code": code,
                "name": fund.get("name") or code,
                "market": fund.get("market") or "",
                "sector": fund.get("theme") or None,
                "shares": current,
                "baseline_shares": baseline,
                "delta_shares": delta,
                "change_rate": rate,
                "change_status": rate_status,
                "price_close": current_price,
                "baseline_price_close": baseline_price,
                "price_change_rate": price_rate,
                "price_status": price_status,
                "direction": "increase" if delta is not None and delta > 0 else "decrease" if delta is not None and delta < 0 else "flat",
                "special_event": None,
                "special_event_status": "NO_RELIABLE_SOURCE",
                "disclosure_lag_status": "SOURCE_DATE_ONLY" if fund.get("type") == "跨境" else "NOT_FLAGGED",
                "quality_status": "OK" if rate_status == "OK" else "PARTIAL",
                "source": "local_sqlite",
                "synced_at": now,
            }
            ranking_rows.append(row)

            code_dates = [day for day in dates if day in shares[code]]
            rates = []
            for index in range(1, len(code_dates)):
                value, _ = change_rate(shares[code][code_dates[index]], shares[code][code_dates[index - 1]])
                rates.append(value)
            ratio, ratio_status = anomaly_ratio(rate, rates[:-1], config.anomaly_min_history)
            anomaly = {
                "_id": _doc_id(release_id, code), **common,
                "statistic_date": statistic_date,
                "code": code,
                "name": row["name"],
                "change_rate": rate,
                "history_median_abs_rate": (abs(rate) / ratio if ratio not in (None, 0) else None),
                "anomaly_ratio": ratio,
                "price_change_rate": price_rate,
                "status": "SPECIAL_EVENT_EXCLUDED" if row["special_event"] else ratio_status,
                "special_event": row["special_event"],
                "qualified": bool(
                    not row["special_event"] and ratio is not None and
                    ratio >= config.anomaly_ratio_threshold and abs(rate or 0) >= config.anomaly_abs_pct_threshold / 100
                ),
                "rule": {
                    "window": config.anomaly_window,
                    "min_history": config.anomaly_min_history,
                    "ratio_threshold": config.anomaly_ratio_threshold,
                    "abs_pct_threshold": config.anomaly_abs_pct_threshold,
                    "excludes_current": True,
                },
            }
            anomaly_rows.append(anomaly)
            streak = {"_id": _doc_id(release_id, code), **common, "statistic_date": statistic_date,
                      "code": code, "name": row["name"], **_streak(code, dates, shares)}
            streak_rows.append(streak)
            sector = fund.get("theme") or ""
            if sector:
                sector_latest_codes[sector].add(code)
                if rate is not None:
                    daily_rates_by_sector[sector].append(rate)

        stable_rank([row for row in ranking_rows if row["direction"] == "increase"], "delta_shares", True)
        stable_rank([row for row in ranking_rows if row["direction"] == "decrease"], "delta_shares", False)
        stable_rank([row for row in streak_rows if row.get("direction") == "increase"], "days", True)
        stable_rank([row for row in streak_rows if row.get("direction") == "decrease"], "days", True)
        stable_rank([row for row in anomaly_rows if row.get("qualified")], "anomaly_ratio", True)

        sectors = []
        for sector in sorted(sector_latest_codes):
            rates = daily_rates_by_sector[sector]
            sample_count = len(sector_latest_codes[sector])
            valid_count = len(rates)
            sectors.append({
                "_id": _doc_id(release_id, hashlib.sha1(sector.encode("utf-8")).hexdigest()[:12]), **common,
                "statistic_date": statistic_date,
                "sector": sector,
                "median_change_rate": statistics.median(rates) if rates else None,
                "valid_sample_count": valid_count,
                "missing_sample_count": sample_count - valid_count,
                "sample_count": sample_count,
                "classification_source": "funds.theme",
                "classification_overlaps": False,
            })
        stable_rank(sectors, "median_change_rate", True)

        if correction_from:
            history_dates = [day for day in all_dates if day >= correction_from and day <= statistic_date]
        elif initial:
            history_dates = all_dates[-config.history_days:]
        else:
            history_dates = [statistic_date]
        hist_placeholders = ",".join("?" for _ in history_dates)
        history = []
        if history_dates:
            history_scale = {(row["date"], row["code"]): float(row["shares"]) for row in conn.execute(
                f"SELECT date,code,shares FROM fund_scale WHERE date IN ({hist_placeholders})", history_dates
            )}
            history_price = {(row["date"], row["code"]): float(row["close"]) for row in conn.execute(
                f"SELECT date,code,close FROM fund_price WHERE date IN ({hist_placeholders})", history_dates
            )}
            for date, code in sorted(set(history_scale) | set(history_price)):
                history.append({
                    "_id": _doc_id(code, date, str(release_seq)), **common,
                    "date": date,
                    "code": code,
                    "shares": history_scale.get((date, code)),
                    "close": history_price.get((date, code)),
                    "share_status": "OK" if (date, code) in history_scale else "MISSING",
                    "price_status": "OK" if (date, code) in history_price else "MISSING",
                    "source": "local_sqlite",
                    "synced_at": now,
                })

        coverage = {
            "fund_total": len(funds),
            "latest_share_count": len(latest_codes),
            "comparable_count": len(comparable_codes),
            "latest_price_count": sum(statistic_date in values for values in prices.values()),
            "classification_count": sum(bool(item.get("sector")) for item in masters),
        }
        status = {
            "_id": "current",
            **common,
            "statistic_date": statistic_date,
            "baseline_date": baseline_date,
            "updated_at": now,
            "coverage": coverage,
            "quality_status": "COMPLETE_FOR_COVERED_SAMPLE" if comparable_codes else "UNAVAILABLE",
            "disclaimer": DISCLAIMER,
            "share_unit": "份",
            "display_share_unit": "亿份",
            "price_currency": "CNY",
            "price_adjustment": "前复权",
            "source": "etf.db/fund_scale+fund_price",
            "master_hash": _hash_payload([{key: value for key, value in row.items() if key != "updated_at"} for row in masters]),
            "date_basis": "fund_scale.date (source statistic date, never collection clock)",
            "disclosure_date_verification": "SOURCE_DATE_ONLY",
            "known_limitations": ["no corporate-action adjustment source", "cross-border disclosure lag not independently modeled"],
        }
        batch = {
            "_id": release_id, **common,
            "state": "prepared",
            "created_at": now,
            "statistic_date": statistic_date,
            "baseline_date": baseline_date,
            "source_signature": source_signature,
            "counts": {},
        }
        collections = {
            "etf_master": masters,
            "etf_history": history,
            "ranking_snapshots": ranking_rows,
            "streak_snapshots": streak_rows,
            "anomaly_snapshots": anomaly_rows,
            "sector_snapshots": sectors,
        }
        batch["counts"] = {name: len(rows) for name, rows in collections.items()}
        batch["payload_hash"] = _hash_payload({name: [_hash_payload(row) for row in rows] for name, rows in collections.items()})
        config_payload = asdict(config)
        config_payload["db_path"] = str(config.db_path)
        config_payload["api_key"] = ""  # credentials are never exported or logged
        return {"release": status, "batch": batch, "collections": collections, "config": config_payload}
    finally:
        conn.close()

