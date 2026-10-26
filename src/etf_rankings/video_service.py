"""Read-only video project builder for the local ETF ranking release.

The service deliberately consumes only the current published calculation batch.
It does not calculate rankings, mutate SQLite, or make investment conclusions.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Any


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_SCENES = ("open", "increase", "decrease", "focus", "closing")
SCENE_TIMINGS = {
    "open": {"duration": 2.8, "transition": 0.3},
    "increase": {"duration": 5.0, "transition": 0.3},
    "decrease": {"duration": 5.0, "transition": 0.3},
    "focus": {"duration": 5.0, "transition": 0.3},
    "closing": {"duration": 3.4, "transition": 0.0},
}
DISCLAIMER = "份额变化不代表未来涨跌，不等同资金净流入。"
CANVAS = {
    "width": 1080,
    "height": 1920,
    "safe_area": {"left": 75, "right": 1005, "top": 110, "content_bottom": 1540,
                  "subtitle_top": 1540, "subtitle_bottom": 1740, "footer_top": 1760,
                  "footer_bottom": 1840},
}


class VideoProjectError(Exception):
    """A user-facing validation or availability error for video materials."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _date(value: str) -> str:
    if not DATE_RE.fullmatch(value or ""):
        raise VideoProjectError("BAD_REQUEST", "日期格式须为 YYYY-MM-DD")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise VideoProjectError("BAD_REQUEST", "日期无效") from exc
    return value


def _release(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM etf_calc_publish_status WHERE id='current'").fetchone()
    if not row:
        raise VideoProjectError("NO_DATA", "本地龙虎榜尚未发布，不能制作视频", 404)
    result = dict(row)
    try:
        result["coverage"] = json.loads(result.pop("coverage_json"))
    except (TypeError, json.JSONDecodeError):
        result["coverage"] = {}
    return result


def available_dates(db_path: Path, limit: int = 3660) -> list[str]:
    """Return real published transaction dates, newest first."""
    with _connect(db_path) as conn:
        release = _release(conn)
        rows = conn.execute(
            """SELECT date FROM etf_derived_daily WHERE batch_id=?
               GROUP BY date ORDER BY date DESC LIMIT ?""",
            (release["batch_id"], max(1, min(int(limit), 5000))),
        ).fetchall()
        return [row["date"] for row in rows]


def _eligible(row: dict[str, Any]) -> bool:
    return (
        row.get("quality_status") == "OK"
        and row.get("change_status") == "OK"
        and row.get("change_rate") is not None
        and not bool(row.get("special_event"))
    )


def _focus_candidate(rows: list[dict[str, Any]], code: str | None = None) -> tuple[dict[str, Any] | None, str, str]:
    eligible = [row for row in rows if _eligible(row)]
    if code:
        selected = next((row for row in eligible if row["code"] == code), None)
        if not selected:
            raise VideoProjectError("BAD_REQUEST", "手动焦点ETF不在当日可靠样本内")
        return selected, "manual", "人工选择的可靠ETF"

    anomaly = [row for row in eligible if bool(row.get("anomaly_qualified")) and row.get("anomaly_ratio") is not None]
    if anomaly:
        return sorted(anomaly, key=lambda row: (-float(row["anomaly_ratio"]), -abs(float(row["change_rate"])), row["code"]))[0], "auto", "异动倍数居前"
    streak = [row for row in eligible if row.get("streak_status") == "OK" and int(row.get("streak_days") or 0) >= 3]
    if streak:
        selected = sorted(streak, key=lambda row: (-int(row["streak_days"]), -abs(float(row.get("streak_change_rate") or 0)), row["code"]))[0]
        direction = "增加" if selected.get("streak_direction") == "increase" else "减少"
        return selected, "auto", f"连续{direction}{selected['streak_days']}个交易日"
    if eligible:
        return sorted(eligible, key=lambda row: (-abs(float(row["change_rate"])), row["code"]))[0], "auto", "当日份额变化幅度居前"
    return None, "auto", "当日可靠样本不足，展示覆盖情况"


def _history(conn: sqlite3.Connection, code: str, date: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(
        """WITH dates AS (
               SELECT DISTINCT date FROM fund_scale WHERE date<=? ORDER BY date DESC LIMIT 7
           )
           SELECT dates.date,s.shares,p.close
           FROM dates LEFT JOIN fund_scale s ON s.date=dates.date AND s.code=?
           LEFT JOIN fund_price p ON p.date=dates.date AND p.code=?
           ORDER BY dates.date""",
        (date, code, code),
    )]


def _rankings(rows: list[dict[str, Any]], conn: sqlite3.Connection, date: str, batch_id: str) -> dict[str, list[dict[str, Any]]]:
    increase = sorted((row for row in rows if row.get("direction") == "increase"),
                      key=lambda row: (-float(row["delta_shares"]), row["code"]))[:5]
    decrease = sorted((row for row in rows if row.get("direction") == "decrease"),
                      key=lambda row: (float(row["delta_shares"]), row["code"]))[:5]
    sectors = [dict(row) for row in conn.execute(
        """SELECT * FROM etf_sector_daily WHERE date=? AND batch_id=? ORDER BY rank,sector LIMIT 5""",
        (date, batch_id),
    )]
    return {"increase": increase, "decrease": decrease, "sectors": sectors}


def _subtitle(text: str, index: int, start: float, end: float) -> dict[str, Any]:
    return {"index": index, "start": round(start, 3), "end": round(end, 3), "text": text}


def _scene_text(project: dict[str, Any]) -> list[dict[str, Any]]:
    rankings = project["rankings"]
    focus = project["focus"]
    top_inc = rankings["increase"][0] if rankings["increase"] else None
    top_dec = rankings["decrease"][0] if rankings["decrease"] else None
    open_line = f"{project['trade_date']}，ETF份额龙虎榜。"
    inc_line = f"份额增加榜中，{top_inc['name']}居首。" if top_inc else "当日无可用份额增加榜。"
    dec_line = f"份额减少榜中，{top_dec['name']}变化居前。" if top_dec else "当日无可用份额减少榜。"
    if focus.get("row"):
        row = focus["row"]
        focus_line = f"今日观察{row['name']}，份额变化{float(row['change_rate']) * 100:+.2f}%。"
    else:
        focus_line = "当日可靠样本不足，请结合数据覆盖情况观察。"
    closing_line = "份额变化不等同资金净流入。"
    lines = [open_line, inc_line, dec_line, focus_line, closing_line]
    start = 0.0
    result = []
    for index, (scene, line) in enumerate(zip(DEFAULT_SCENES, lines), start=1):
        duration = SCENE_TIMINGS[scene]["duration"]
        result.append({"scene": scene, "voice": line,
                       "subtitle": _subtitle(line, index, start, start + duration)})
        start += duration - SCENE_TIMINGS[scene]["transition"]
    return result


def build_video_project(db_path: Path, requested_date: str | None = None,
                        focus_code: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a deterministic project from one existing published calculation batch."""
    with _connect(db_path) as conn:
        release = _release(conn)
        date = _date(requested_date or release["statistic_date"])
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM etf_derived_daily WHERE date=? AND batch_id=?", (date, release["batch_id"])
        )]
        if not rows:
            raise VideoProjectError("NO_DATA", "所选日期没有当前已发布的派生结果", 404)
        focus_row, focus_mode, focus_reason = _focus_candidate(rows, focus_code)
        baseline_date = next((row.get("baseline_date") for row in rows if row.get("baseline_date")), None)
        project: dict[str, Any] = {
            "schema_version": "3.0",
            "preset": {"id": "daily-20s", "target_duration": 20.0,
                       "accepted_duration_range": [18.0, 24.0]},
            "source": {"database": str(db_path.name), "batch_id": release["batch_id"],
                       "calc_version": release.get("calc_version"), "trade_date": date,
                       "baseline_date": baseline_date, "release_statistic_date": release["statistic_date"],
                       "updated_at": release.get("updated_at")},
            "trade_date": date,
            "baseline_date": baseline_date,
            "batch_id": release["batch_id"],
            "quality_status": release["quality_status"],
            "coverage": release["coverage"],
            "canvas": CANVAS,
            "brand": {"title": "ETF份额龙虎榜", "subtitle": "微信小程序 · 基础榜单可用",
                      "closing": "每日更新 · 以页面实际统计日为准"},
            "disclaimer": DISCLAIMER,
            "rankings": _rankings(rows, conn, date, release["batch_id"]),
            "focus": {"mode": focus_mode, "code": focus_row["code"] if focus_row else None,
                      "name": focus_row["name"] if focus_row else None, "reason": focus_reason,
                      "row": focus_row, "history": _history(conn, focus_row["code"], date) if focus_row else []},
            "scenes": [{"id": scene, "order": index, **SCENE_TIMINGS[scene]}
                       for index, scene in enumerate(DEFAULT_SCENES, start=1)],
        }
        project["narration"] = _scene_text(project)
        total = sum(item["duration"] for item in project["scenes"]) - sum(item["transition"] for item in project["scenes"])
        audit = {
            "publishable": release["quality_status"] == "COMPLETE_FOR_COVERED_SAMPLE",
            "trade_date": date, "baseline_date": baseline_date, "batch_id": release["batch_id"],
            "row_count": len(rows), "reliable_count": len([row for row in rows if _eligible(row)]),
            "increase_count": len([row for row in rows if row.get("direction") == "increase"]),
            "decrease_count": len([row for row in rows if row.get("direction") == "decrease"]),
            "focus_reason": focus_reason, "planned_duration": round(total, 3),
            "source": "etf.db current published calculation batch",
        }
        return project, audit


def validate_project_request(payload: dict[str, Any], db_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate untrusted workbench input and rebuild all factual fields."""
    if not isinstance(payload, dict):
        raise VideoProjectError("BAD_REQUEST", "请求体必须为JSON对象")
    allowed = {"date", "preset", "focus_mode", "focus_code", "scene_ids", "render_mode"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise VideoProjectError("BAD_REQUEST", f"不支持的字段：{','.join(unknown)}")
    if payload.get("preset", "daily-20s") != "daily-20s":
        raise VideoProjectError("BAD_REQUEST", "当前仅支持daily-20s预设")
    focus_mode = payload.get("focus_mode", "auto")
    if focus_mode not in {"auto", "manual"}:
        raise VideoProjectError("BAD_REQUEST", "焦点模式无效")
    code = payload.get("focus_code") if focus_mode == "manual" else None
    if code is not None and not re.fullmatch(r"\d{6}", str(code)):
        raise VideoProjectError("BAD_REQUEST", "ETF代码必须为6位数字")
    scenes = payload.get("scene_ids", list(DEFAULT_SCENES))
    if not isinstance(scenes, list) or any(item not in DEFAULT_SCENES for item in scenes):
        raise VideoProjectError("BAD_REQUEST", "场景列表无效")
    if not scenes:
        raise VideoProjectError("BAD_REQUEST", "至少选择一个场景")
    if payload.get("render_mode", "all") not in {"data", "stills", "all"}:
        raise VideoProjectError("BAD_REQUEST", "渲染模式无效")
    project, audit = build_video_project(db_path, payload.get("date"), str(code) if code else None)
    project["requested_scenes"] = scenes
    return project, audit
