from __future__ import annotations

import copy
import csv
import math
import re
import sqlite3
import statistics
from collections import defaultdict
from functools import lru_cache
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "etf.db"
OVERRIDES_PATH = PROJECT_ROOT / "design" / "etf_rankings" / "prototype" / "config" / "classification_overrides.csv"
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
SCHEMA_VERSION = "1.0"
ROBUST_Z_THRESHOLD = 2.5


class RankingInputError(ValueError):
    """The caller supplied an invalid ranking request."""


class RankingNotFoundError(LookupError):
    """The requested trade date has no share data."""


def _clean(value: float | None, digits: int = 3) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return (new / old - 1) * 100


def _robust_z(current: float, history: list[float]) -> float | None:
    if len(history) < 60:
        return None
    median = statistics.median(history)
    mad = statistics.median(abs(value - median) for value in history)
    if mad == 0:
        return 0.0 if current == median else None
    return (current - median) / (1.4826 * mad)


def _load_overrides() -> dict[str, dict[str, str]]:
    if not OVERRIDES_PATH.exists():
        return {}
    with OVERRIDES_PATH.open(encoding="utf-8-sig", newline="") as handle:
        return {row["code"]: row for row in csv.DictReader(handle)}


def _asset_class(name: str, theme: str, code: str, overrides: dict[str, dict[str, str]]) -> str:
    if code in overrides:
        return overrides[code]["asset_class"]
    if any(key in name for key in ("货币", "现金", "快线", "快钱", "保证金")):
        return "money"
    if any(key in name for key in ("债", "短融", "政金", "可转债")):
        return "bond"
    if "黄金股" not in name and any(key in name for key in ("黄金ETF", "黄金基金", "豆粕ETF", "有色期货", "能源化工")):
        return "commodity"
    if theme == "跨境/海外" or any(key in name for key in ("恒生", "港股", "纳指", "纳斯达克", "标普", "道琼斯", "日经", "德国", "法国", "沙特", "印度", "韩国", "东南亚", "美国", "日本", "中概", "亚太")):
        return "cross_border"
    return "equity"


def _signature() -> tuple[int, int, int, int]:
    db_stat = DB_PATH.stat()
    wal_path = DB_PATH.with_name(f"{DB_PATH.name}-wal")
    wal_stat = wal_path.stat() if wal_path.exists() else None
    return (
        db_stat.st_mtime_ns,
        db_stat.st_size,
        wal_stat.st_mtime_ns if wal_stat else 0,
        wal_stat.st_size if wal_stat else 0,
    )


def _connect_read_only() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _with_rank(rows: list[dict]) -> list[dict]:
    return [{**row, "rank": index} for index, row in enumerate(rows, 1)]


def build_rankings(requested_date: str | None = None) -> dict:
    """Return a JSON-safe daily/weekly ranking payload from the read-only ETF database."""
    if requested_date is not None and not DATE_PATTERN.fullmatch(requested_date):
        raise RankingInputError("日期格式须为 YYYY-MM-DD")
    signature = _signature()
    return copy.deepcopy(_build_rankings_cached(requested_date or "", signature))


@lru_cache(maxsize=32)
def _build_rankings_cached(requested_date: str, _db_signature: tuple[int, int, int, int]) -> dict:
    overrides = _load_overrides()
    conn = _connect_read_only()
    try:
        latest = requested_date or conn.execute("SELECT MAX(date) FROM fund_scale").fetchone()[0]
        if not latest:
            raise RankingNotFoundError("fund_scale 没有可用数据")
        if not conn.execute("SELECT 1 FROM fund_scale WHERE date=? LIMIT 1", (latest,)).fetchone():
            raise RankingNotFoundError(f"所选日期没有份额数据：{latest}")
        previous = conn.execute("SELECT MAX(date) FROM fund_scale WHERE date < ?", (latest,)).fetchone()[0]
        if not previous:
            raise RankingInputError("没有更早的交易日可供比较")

        dates = [row[0] for row in conn.execute(
            "SELECT DISTINCT date FROM fund_scale WHERE date<=? ORDER BY date DESC LIMIT 62", (latest,)
        )][::-1]
        date_set = set(dates)
        week_dates = dates[-6:] if len(dates) >= 6 else []
        week_start = week_dates[0] if week_dates else None

        fund_meta = {
            row["code"]: dict(row)
            for row in conn.execute("SELECT code,name,theme FROM funds")
        }
        placeholders = ",".join("?" for _ in dates)
        share_rows = conn.execute(
            f"SELECT date,code,shares FROM fund_scale WHERE date IN ({placeholders}) ORDER BY code,date", dates
        ).fetchall()
        price_rows = conn.execute(
            f"SELECT date,code,close FROM fund_price WHERE date IN ({placeholders}) ORDER BY code,date", dates
        ).fetchall()
        shares_by_code: dict[str, dict[str, float]] = defaultdict(dict)
        prices_by_code: dict[str, dict[str, float]] = defaultdict(dict)
        for row in share_rows:
            shares_by_code[row["code"]][row["date"]] = row["shares"]
        for row in price_rows:
            prices_by_code[row["code"]][row["date"]] = row["close"]

        counts = {"increase": 0, "decrease": 0, "flat": 0}
        records: list[dict] = []
        for code, series in shares_by_code.items():
            if latest not in series or previous not in series:
                continue
            current, prior = series[latest], series[previous]
            delta = current - prior
            counts["increase" if delta > 0 else "decrease" if delta < 0 else "flat"] += 1
            meta = fund_meta.get(code, {"name": code, "theme": ""})
            name = str(meta.get("name") or code)
            theme = str(meta.get("theme") or "")
            close = prices_by_code[code].get(latest)
            previous_close = prices_by_code[code].get(previous)

            complete_62 = len(dates) == 62 and all(day in series for day in dates)
            historical_changes = []
            if complete_62:
                historical_changes = [
                    (series[dates[index]] - series[dates[index - 1]]) / 1e8
                    for index in range(1, len(dates) - 1)
                ]
            robust_z = _robust_z(delta / 1e8, historical_changes)
            record = {
                "code": code,
                "name": name,
                "asset_class": _asset_class(name, theme, code, overrides),
                "classification_verified": code in overrides,
                "current_shares_yi": _clean(current / 1e8),
                "delta_shares_yi": _clean(delta / 1e8),
                "share_change_pct": _clean(_pct(current, prior)),
                "close": _clean(close, 4),
                "price_change_pct": _clean(_pct(close, previous_close)),
                "reference_value_yi": _clean(delta * close / 1e8 if close is not None else None),
                "robust_z_60": _clean(robust_z, 2),
                "is_abnormal": bool(robust_z is not None and abs(robust_z) >= ROBUST_Z_THRESHOLD),
            }
            if week_dates and all(day in series for day in week_dates) and close is not None:
                week_shares = [series[day] for day in week_dates]
                changes = [week_shares[index] - week_shares[index - 1] for index in range(1, len(week_shares))]
                record.update({
                    "delta_5d_shares_yi": _clean((current - week_shares[0]) / 1e8),
                    "share_change_5d_pct": _clean(_pct(current, week_shares[0])),
                    "reference_5d_yi": _clean((current - week_shares[0]) * close / 1e8),
                    "positive_days_5": sum(change > 0 for change in changes),
                    "negative_days_5": sum(change < 0 for change in changes),
                    "series_5": [{"date": day, "shares_yi": _clean(series[day] / 1e8)} for day in week_dates],
                })
            records.append(record)

        priced = [record for record in records if record["reference_value_yi"] is not None]
        daily_increase = _with_rank(sorted(
            (record for record in priced if record["reference_value_yi"] > 0),
            key=lambda record: (-record["reference_value_yi"], record["code"]),
        )[:10])
        daily_decrease = _with_rank(sorted(
            (record for record in priced if record["reference_value_yi"] < 0),
            key=lambda record: (record["reference_value_yi"], record["code"]),
        )[:10])
        weekly_candidates = [record for record in records if record.get("reference_5d_yi") is not None]
        weekly_increase = _with_rank(sorted(
            (record for record in weekly_candidates if record["positive_days_5"] == 5),
            key=lambda record: (-record["reference_5d_yi"], record["code"]),
        )[:10])
        weekly_decrease = _with_rank(sorted(
            (record for record in weekly_candidates if record["delta_5d_shares_yi"] < 0),
            key=lambda record: (record["reference_5d_yi"], record["code"]),
        )[:10])

        scale_count = conn.execute("SELECT COUNT(*) FROM fund_scale WHERE date=?", (latest,)).fetchone()[0]
        price_match_count = conn.execute(
            "SELECT COUNT(*) FROM fund_scale s JOIN fund_price p USING(date,code) WHERE s.date=?", (latest,)
        ).fetchone()[0]
        warnings: list[str] = []
        if scale_count - price_match_count:
            warnings.append(f"{scale_count - price_match_count}只ETF缺少当日收盘价，未进入参考市值榜。")
        if not week_dates:
            warnings.append("历史不足6个交易日，周榜暂不可用。")
        warnings.append("行业主题分类仍待核验，主题榜本期未启用。")
        return {
            "schema_version": SCHEMA_VERSION,
            "meta": {
                "trade_date": latest,
                "previous_trade_date": previous,
                "week_start": week_start,
                "source": "项目本地 etf.db；份额源于交易所数据，价格源于本地通达信日线",
                "disclaimer": "份额变化×当日收盘价为收盘参考市值，不等于真实资金净流入或基金资产净值变化。",
            },
            "audit": {
                "scale_count": scale_count,
                "price_match_count": price_match_count,
                "missing_price_count": scale_count - price_match_count,
                "warnings": warnings,
            },
            "daily": {"counts": counts, "increase": daily_increase, "decrease": daily_decrease},
            "weekly": {"continuous_increase": weekly_increase, "cumulative_decrease": weekly_decrease},
        }
    finally:
        conn.close()
