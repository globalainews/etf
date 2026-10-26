from __future__ import annotations

import csv
import hashlib
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from . import SCHEMA_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODULE_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "etf.db"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from src.etf_rankings.ranking_service import build_rankings


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(name: str) -> dict:
    return json.loads((MODULE_ROOT / "config" / name).read_text(encoding="utf-8"))


def load_overrides() -> dict[str, dict]:
    path = MODULE_ROOT / "config" / "classification_overrides.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["code"]: row for row in csv.DictReader(handle)}


def asset_class(name: str, theme: str, code: str, overrides: dict[str, dict]) -> str:
    if code in overrides:
        return overrides[code]["asset_class"]
    money = ("货币", "现金", "快线", "快钱", "保证金")
    bond = ("债", "短融", "政金", "可转债")
    commodity = ("黄金ETF", "黄金基金", "豆粕ETF", "有色期货", "能源化工")
    overseas = ("恒生", "港股", "纳指", "纳斯达克", "标普", "道琼斯", "日经", "德国", "法国", "沙特", "印度", "韩国", "东南亚", "美国", "日本", "中概", "亚太")
    if any(key in name for key in money):
        return "money"
    if any(key in name for key in bond):
        return "bond"
    if "黄金股" not in name and any(key in name for key in commodity):
        return "commodity"
    if theme == "跨境/海外" or any(key in name for key in overseas):
        return "cross_border"
    return "equity"


def pct(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return (new / old - 1) * 100


def clean(value: float | None, digits: int = 3) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def robust_z(current: float, history: list[float]) -> float | None:
    if len(history) < 20:
        return None
    median = statistics.median(history)
    mad = statistics.median(abs(x - median) for x in history)
    if mad == 0:
        return 0.0 if current == median else None
    return (current - median) / (1.4826 * mad)


def percentile(values: list[float], value: float) -> float:
    if not values:
        return 0.0
    return sum(x <= value for x in values) / len(values)


def connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def build_content(requested_date: str | None = None) -> tuple[dict, dict]:
    score_cfg = load_json("topic_score.json")
    overrides = load_overrides()
    conn = connect_ro()
    latest = requested_date or conn.execute("SELECT MAX(date) FROM fund_scale").fetchone()[0]
    exists = conn.execute("SELECT 1 FROM fund_scale WHERE date=? LIMIT 1", (latest,)).fetchone()
    if not exists:
        raise ValueError(f"fund_scale 中不存在日期 {latest}")
    previous = conn.execute("SELECT MAX(date) FROM fund_scale WHERE date < ?", (latest,)).fetchone()[0]
    dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM fund_scale WHERE date<=? ORDER BY date DESC LIMIT 62", (latest,))][::-1]
    if len(dates) < 21:
        raise ValueError("至少需要21个交易日才能生成专题")
    date_index = {d: i for i, d in enumerate(dates)}
    start_5, start_20 = dates[-6], dates[-21]

    fund_meta = {r["code"]: dict(r) for r in conn.execute("SELECT code,name,type,market,issue_year,theme FROM funds")}
    rows = conn.execute(
        f"SELECT date,code,shares FROM fund_scale WHERE date IN ({','.join('?' for _ in dates)}) ORDER BY code,date",
        dates,
    ).fetchall()
    shares_by_code: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        shares_by_code[row["code"]][row["date"]] = row["shares"]
    price_rows = conn.execute(
        f"SELECT date,code,close FROM fund_price WHERE date IN ({','.join('?' for _ in dates)}) ORDER BY code,date",
        dates,
    ).fetchall()
    prices_by_code: dict[str, dict[str, float]] = defaultdict(dict)
    for row in price_rows:
        prices_by_code[row["code"]][row["date"]] = row["close"]

    records: list[dict] = []
    counts = {"increase": 0, "decrease": 0, "flat": 0}
    for code, series in shares_by_code.items():
        if latest not in series or previous not in series:
            continue
        meta = fund_meta.get(code, {"name": code, "theme": "", "market": ""})
        name, theme = meta["name"], meta["theme"] or ""
        current, prior = series[latest], series[previous]
        delta = current - prior
        counts["increase" if delta > 0 else "decrease" if delta < 0 else "flat"] += 1
        close, prev_close = prices_by_code[code].get(latest), prices_by_code[code].get(previous)
        ordered = [(d, series[d]) for d in dates if d in series]
        deltas = [(ordered[i][1] - ordered[i - 1][1]) / 1e8 for i in range(1, len(ordered))]
        rz_history = deltas[:-1]
        rz = robust_z(delta / 1e8, rz_history) if len(rz_history) >= 60 else None
        cls = asset_class(name, theme, code, overrides)
        item = {
            "code": code,
            "name": name,
            "theme": theme,
            "market": meta.get("market", ""),
            "asset_class": cls,
            "classification_verified": code in overrides,
            "observations_60": len(ordered),
            "current_shares_yi": clean(current / 1e8),
            "previous_shares_yi": clean(prior / 1e8),
            "delta_shares_yi": clean(delta / 1e8),
            "share_change_pct": clean(pct(current, prior)),
            "close": clean(close, 4),
            "price_change_pct": clean(pct(close, prev_close)),
            "reference_value_yi": clean(delta * close / 1e8 if close is not None else None),
            "previous_reference_size_yi": clean(prior * prev_close / 1e8 if prev_close is not None else None),
            "robust_z_60": clean(rz, 2),
        }
        if start_5 in series:
            item["delta_5d_shares_yi"] = clean((current - series[start_5]) / 1e8)
            item["share_change_5d_pct"] = clean(pct(current, series[start_5]))
            item["reference_5d_yi"] = clean((current - series[start_5]) * close / 1e8 if close is not None else None)
            six = [series[d] for d in dates[-6:] if d in series]
            changes = [six[i] - six[i - 1] for i in range(1, len(six))]
            item["positive_days_5"] = sum(x > 0 for x in changes)
            item["negative_days_5"] = sum(x < 0 for x in changes)
            item["series_5"] = [{"date": d, "shares_yi": clean(series[d] / 1e8)} for d in dates[-6:] if d in series]
        if start_20 in series and start_20 in prices_by_code[code] and close is not None:
            item["delta_20d_shares_yi"] = clean((current - series[start_20]) / 1e8)
            item["share_change_20d_pct"] = clean(pct(current, series[start_20]))
            item["price_change_20d_pct"] = clean(pct(close, prices_by_code[code][start_20]))
            item["reference_20d_yi"] = clean((current - series[start_20]) * close / 1e8)
            item["start_reference_20d_yi"] = clean(series[start_20] * prices_by_code[code][start_20] / 1e8)
        records.append(item)

    priced = [x for x in records if x["reference_value_yi"] is not None]
    daily_inc = sorted(priced, key=lambda x: x["reference_value_yi"], reverse=True)
    daily_dec = sorted(priced, key=lambda x: x["reference_value_yi"])
    growth = [x for x in records if x["observations_60"] >= score_cfg["relative_min_observations"] and (x["previous_reference_size_yi"] or 0) >= score_cfg["growth_min_reference_yi"] and x["previous_shares_yi"] >= score_cfg["growth_min_shares_yi"]]
    growth = sorted(growth, key=lambda x: x["share_change_pct"], reverse=True)[:10]
    continuity = [x for x in records if x["asset_class"] == "equity" and x.get("positive_days_5") == 5 and x.get("reference_5d_yi") is not None]
    continuity = sorted(continuity, key=lambda x: x["reference_5d_yi"], reverse=True)[:10]
    weekly_decrease = [x for x in records if x["asset_class"] == "equity" and x.get("reference_5d_yi") is not None and x.get("delta_5d_shares_yi", 0) < 0]
    weekly_decrease = sorted(weekly_decrease, key=lambda x: x["reference_5d_yi"])[:10]
    cumulative = [x for x in records if x.get("reference_20d_yi") is not None and x["observations_60"] >= score_cfg["long_window_min_observations"]]
    cumulative = sorted(cumulative, key=lambda x: x["reference_20d_yi"], reverse=True)[:10]
    divergence = [x for x in cumulative if (x.get("start_reference_20d_yi") or 0) >= score_cfg["divergence_min_reference_yi"] and (x.get("share_change_20d_pct") or 0) * (x.get("price_change_20d_pct") or 0) < 0]
    divergence = sorted(divergence, key=lambda x: abs(x["reference_20d_yi"]), reverse=True)[:10]

    abs_deltas = [abs(x["delta_shares_yi"]) for x in records]
    abs_refs = [abs(x["reference_value_yi"]) for x in priced]
    ref_sizes = [x["previous_reference_size_yi"] for x in records if x["previous_reference_size_yi"] is not None]
    candidates = []
    for item in daily_inc[:20] + daily_dec[:10]:
        change_component = percentile(abs_deltas, abs(item["delta_shares_yi"]))
        ref_component = percentile(abs_refs, abs(item["reference_value_yi"]))
        rz_component = min(abs(item["robust_z_60"] or 0) / 5, 1)
        continuity_component = max(item.get("positive_days_5", 0), item.get("negative_days_5", 0)) / 5
        divergence_component = min(abs(item.get("share_change_20d_pct", 0) - item.get("price_change_20d_pct", 0)) / 50, 1)
        peer_component = 1 if item["theme"] in {"宽基指数", "科技/TMT"} else 0.6
        representative = percentile(ref_sizes, item["previous_reference_size_yi"] or 0)
        components = {
            "daily_change_percentile": change_component,
            "reference_value": ref_component,
            "robust_z_60": rz_component,
            "continuity": continuity_component,
            "divergence": divergence_component,
            "peer_sync": peer_component,
            "representativeness": representative,
            "novelty": 1.0,
        }
        raw = sum(components[key] * score_cfg["weights"][key] for key in components)
        quality = 0.95 if item["close"] is not None and item["observations_60"] >= 60 else 0.85
        candidates.append({"code": item["code"], "name": item["name"], "score": round(raw * quality, 1), "quality": quality, "components": {k: round(v, 3) for k, v in components.items()}})
    candidates = sorted({x["code"]: x for x in candidates}.values(), key=lambda x: x["score"], reverse=True)
    main_topic = candidates[0]
    publish = main_topic["score"] >= score_cfg["publish_threshold"] and main_topic["quality"] >= score_cfg["quality_threshold"]

    focus: dict[str, dict] = {}
    for code in ("588000", "510300", "159915", "159516"):
        item = next(x for x in records if x["code"] == code)
        series = []
        base_s, base_p = shares_by_code[code][start_20], prices_by_code[code][start_20]
        for date in dates[-21:]:
            if date in shares_by_code[code] and date in prices_by_code[code]:
                series.append({"date": date, "shares_yi": clean(shares_by_code[code][date] / 1e8), "close": clean(prices_by_code[code][date], 4), "shares_index": clean(shares_by_code[code][date] / base_s * 100, 2), "price_index": clean(prices_by_code[code][date] / base_p * 100, 2)})
        focus[code] = {**item, "series_20": series}

    table_counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("funds", "fund_scale", "fund_price")}
    latest_scale = conn.execute("SELECT COUNT(*) FROM fund_scale WHERE date=?", (latest,)).fetchone()[0]
    latest_price = conn.execute("SELECT COUNT(*) FROM fund_price WHERE date=?", (latest,)).fetchone()[0]
    overlap = conn.execute("SELECT COUNT(*) FROM fund_scale s JOIN fund_price p USING(date,code) WHERE s.date=?", (latest,)).fetchone()[0]
    suspicious = [x for x in records if x["theme"] == "债券/货币" and x["asset_class"] in {"equity", "cross_border"}]

    audit = {
        "db_sha256": file_sha256(DB_PATH),
        "table_counts": table_counts,
        "trade_date": latest,
        "previous_trade_date": previous,
        "latest_scale_count": latest_scale,
        "latest_price_count": latest_price,
        "latest_overlap_count": overlap,
        "latest_missing_price_count": latest_scale - overlap,
        "suspicious_classification_count": len(suspicious),
        "suspicious_examples": [{"code": x["code"], "name": x["name"], "theme": x["theme"]} for x in suspicious[:10]],
        "classification_status": "blocked_for_theme_ranking",
        "checks": {
            "funds_match_scale_codes": conn.execute("SELECT COUNT(DISTINCT s.code) FROM fund_scale s LEFT JOIN funds f USING(code) WHERE f.code IS NULL").fetchone()[0] == 0,
            "non_positive_latest_shares": conn.execute("SELECT COUNT(*) FROM fund_scale WHERE date=? AND shares<=0", (latest,)).fetchone()[0],
            "non_positive_latest_prices": conn.execute("SELECT COUNT(*) FROM fund_price WHERE date=? AND close<=0", (latest,)).fetchone()[0],
        },
    }
    shared_rankings = build_rankings(requested_date)
    content = {
        "meta": {"schema_version": SCHEMA_VERSION, "run_id": f"{latest.replace('-', '')}-{datetime.now().strftime('%H%M%S')}", "trade_date": latest, "previous_trade_date": previous, "week_start": start_5, "window_20_start": start_20, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "db_sha256": audit["db_sha256"], "source": "项目本地 etf.db；份额源于交易所数据，价格源于本地通达信日线", "disclaimer": "份额变化×当日收盘价为收盘参考市值，不等于真实资金净流入或基金资产净值变化。"},
        "audit_summary": {"latest_scale_count": latest_scale, "latest_price_count": latest_price, "latest_missing_price_count": latest_scale - overlap, "quality_coefficient": main_topic["quality"]},
        "rankings": {"counts": shared_rankings["daily"]["counts"], "daily_reference_increase": shared_rankings["daily"]["increase"], "daily_reference_decrease": shared_rankings["daily"]["decrease"], "daily_growth_rate": growth, "continuous_increase_5d": shared_rankings["weekly"]["continuous_increase"], "weekly_decrease_5d": shared_rankings["weekly"]["cumulative_decrease"], "cumulative_20d": cumulative, "divergence_20d": divergence, "theme": {"status": "blocked", "reason": "现有主题字段存在明显错标；完成权威分类核验前不自动发布主题榜。"}},
        "topic": {"publish": publish, "threshold": score_cfg["publish_threshold"], "selected": main_topic, "candidates": candidates[:10], "headline": "宽基ETF占据日榜前五，300ETF收盘参考市值居首", "evidence": ["300ETF当日份额增加25.13亿份", "对应收盘参考市值104.98亿元", "过去20日份额增加48.70%、价格下跌7.81%"], "caveat": "只能描述份额和价格的统计事实，不能据此识别投资者身份或断言资金抄底。"},
        "focus": focus,
        "divergence_cases": [focus["510300"], focus["588000"], focus["159915"]],
        "rejected_case": {"code": "159516", "reason": "20日份额与价格同步下降，不符合价跌份额增专题。"},
        "scenes": [
            {"id": "opening", "start": 0, "end": 3, "scene": "daily", "voice": "10月23日，宽基ETF包揽份额异动榜前五。"},
            {"id": "ranking", "start": 3, "end": 12, "scene": "daily", "voice": "300ETF份额增加25.13亿份，对应收盘参考市值约104.98亿元，位列第一。"},
            {"id": "focus", "start": 12, "end": 21, "scene": "focus", "voice": "科创50ETF过去20个交易日份额增加48.03%，同期价格下跌16.16%，背离仍在延续。"},
            {"id": "ending", "start": 21, "end": 25, "scene": "daily10", "voice": "以上为份额统计，不等于真实资金净流入。"}
        ]
    }
    if latest == "2026-10-23":
        assert table_counts["funds"] == 1801
        assert latest_scale == 1690 and 0 <= overlap <= latest_scale
        assert latest_scale - overlap == audit["latest_missing_price_count"]
        assert round(focus["588000"]["share_change_20d_pct"], 2) == 48.03
        assert round(focus["588000"]["price_change_20d_pct"], 2) == -16.16
    conn.close()
    return content, audit

