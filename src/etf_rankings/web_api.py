from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import sqlite3
from typing import Any

from flask import Blueprint, current_app, jsonify, request

import db
from sync.etf_sync.local_store import LOCAL_USER_ID
from sync.etf_sync.member_store import adopt_remote, list_members, save_member


mini_api = Blueprint("mini_api", __name__, url_prefix="/api/mini")
DISCLAIMER = "份额变化不代表未来涨跌，不等同资金净流入。"
CODE_RE = re.compile(r"^\d{6}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _connect(write: bool = False) -> sqlite3.Connection:
    if write:
        # Pass the path explicitly: db.connect's default is bound at import time,
        # while tests and alternate deployments may intentionally replace DB_PATH.
        conn = db.connect(str(db.DB_PATH))
    else:
        conn = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True, timeout=10)
        conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _success(data: Any, status: int = 200):
    return jsonify({"ok": True, "data": data}), status


def _current(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM etf_calc_publish_status WHERE id='current'"
    ).fetchone()
    if not row:
        raise ApiError("NO_DATA", "本地龙虎榜尚未计算，请先执行全量物化命令", 404)
    result = dict(row)
    result["coverage"] = json.loads(result.pop("coverage_json"))
    # Older local calculation batches did not persist the price coverage field.
    # Derive the real current-day number for display rather than showing a fake
    # zero or an "undefined" placeholder in the mini-program-equivalent UI.
    result["coverage"].setdefault("latest_price_count", conn.execute(
        "SELECT COUNT(DISTINCT code) FROM fund_price WHERE date=? AND close IS NOT NULL",
        (result["statistic_date"],),
    ).fetchone()[0])
    result["disclaimer"] = DISCLAIMER
    try:
        updated = datetime.fromisoformat(result["updated_at"].replace("Z", "+00:00"))
        result["is_stale"] = (datetime.now(timezone.utc) - updated).total_seconds() > 96 * 3600
    except (TypeError, ValueError):
        result["is_stale"] = True
    result.update({"share_unit": "份", "display_share_unit": "亿份",
                   "price_currency": "CNY", "price_adjustment": "前复权"})
    return result


def _status_for_date(conn: sqlite3.Connection, value: str | None) -> dict[str, Any]:
    """Return release metadata plus truthful coverage for one published trading day."""
    result = _current(conn)
    release_date = result["statistic_date"]
    day = _date(value, result)
    snapshot = conn.execute(
        """SELECT baseline_date,COUNT(*) AS share_count,
                  SUM(CASE WHEN change_status='OK' THEN 1 ELSE 0 END) AS comparable_count,
                  SUM(shares) AS total_shares,
                  SUM(CASE WHEN change_status='OK' AND delta_shares>0 THEN 1 ELSE 0 END) AS increase_count,
                  SUM(CASE WHEN change_status='OK' AND delta_shares<0 THEN 1 ELSE 0 END) AS decrease_count,
                  SUM(CASE WHEN change_status='OK' AND delta_shares=0 THEN 1 ELSE 0 END) AS unchanged_count,
                  SUM(CASE WHEN change_status!='OK' THEN 1 ELSE 0 END) AS unavailable_change_count
           FROM etf_derived_daily WHERE date=?""",
        (day,),
    ).fetchone()
    if not snapshot or not snapshot["share_count"]:
        raise ApiError("NO_DATA", "所选日期没有已发布的龙虎榜数据", 404)
    coverage = dict(result["coverage"])
    coverage.update({
        "latest_share_count": int(snapshot["share_count"]),
        "comparable_count": int(snapshot["comparable_count"] or 0),
        "latest_price_count": conn.execute(
            "SELECT COUNT(DISTINCT code) FROM fund_price WHERE date=? AND close IS NOT NULL", (day,)
        ).fetchone()[0],
        "total_shares": snapshot["total_shares"],
        "increase_count": int(snapshot["increase_count"] or 0),
        "decrease_count": int(snapshot["decrease_count"] or 0),
        "unchanged_count": int(snapshot["unchanged_count"] or 0),
        "unavailable_change_count": int(snapshot["unavailable_change_count"] or 0),
    })
    result.update({
        "release_statistic_date": release_date,
        "statistic_date": day,
        "baseline_date": snapshot["baseline_date"],
        "coverage": coverage,
        "is_historical": day != release_date,
    })
    return result


def _date(value: str | None, current: dict[str, Any]) -> str:
    value = (value or current["statistic_date"]).strip()
    if not DATE_RE.fullmatch(value):
        raise ApiError("BAD_REQUEST", "日期格式须为 YYYY-MM-DD")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ApiError("BAD_REQUEST", "日期无效") from exc
    if value > current["statistic_date"]:
        raise ApiError("NO_DATA", "所选日期尚未发布", 404)
    return value


def _code(value: str) -> str:
    value = str(value or "")
    if not CODE_RE.fullmatch(value):
        raise ApiError("BAD_REQUEST", "ETF代码必须为6位数字")
    return value


def _paging() -> tuple[int, int]:
    try:
        page = int(request.args.get("page", 1))
        size = int(request.args.get("pageSize", request.args.get("page_size", 20)))
    except ValueError as exc:
        raise ApiError("BAD_REQUEST", "分页参数无效") from exc
    if not 1 <= page <= 1000 or not 1 <= size <= 50:
        raise ApiError("BAD_REQUEST", "页码须大于0，每页数量须为1到50")
    return page, size


def _paged(conn: sqlite3.Connection, sql: str, params: list[Any], count_sql: str,
           count_params: list[Any], page: int, size: int) -> dict[str, Any]:
    total = conn.execute(count_sql, count_params).fetchone()[0]
    rows = [dict(row) for row in conn.execute(sql + " LIMIT ? OFFSET ?", [*params, size, (page - 1) * size])]
    return {"items": rows, "page": page, "pageSize": size, "total": total,
            "hasMore": page * size < total}


@mini_api.errorhandler(ApiError)
def handle_api_error(error: ApiError):
    return jsonify({"ok": False, "error": {"code": error.code, "message": error.message}}), error.status


@mini_api.errorhandler(Exception)
def handle_unexpected(error: Exception):
    current_app.logger.exception("ETF mini web API failed")
    return jsonify({"ok": False, "error": {"code": "SERVICE_ERROR", "message": "服务暂不可用，请稍后重试"}}), 500


@mini_api.get("/status")
def status():
    with _connect() as conn:
        return _success(_status_for_date(conn, request.args.get("date")))


@mini_api.get("/calendar")
def calendar():
    """Find the immediately previous or next published trading day.

    The date input remains available for arbitrary history, while these small,
    indexed queries make phone navigation follow actual trading days rather
    than calendar days.
    """
    direction = request.args.get("direction", "")
    if direction not in {"previous", "next"}:
        raise ApiError("BAD_REQUEST", "direction 须为 previous 或 next")
    with _connect() as conn:
        release = _current(conn)
        day = _date(request.args.get("date"), release)
        operator, order = ("<", "DESC") if direction == "previous" else (">", "ASC")
        row = conn.execute(
            f"SELECT date FROM etf_derived_daily WHERE date {operator} ? GROUP BY date ORDER BY date {order} LIMIT 1",
            (day,),
        ).fetchone()
        return _success({"date": row["date"] if row else None, "direction": direction,
                         "current_date": day, "latest_date": release["statistic_date"]})


@mini_api.get("/rankings")
def rankings():
    direction = request.args.get("direction", "increase")
    sort = request.args.get("sort", "delta")
    if direction not in {"increase", "decrease"} or sort != "delta":
        raise ApiError("BAD_REQUEST", "方向或排序字段无效")
    page, size = _paging()
    order = "DESC" if direction == "increase" else "ASC"
    with _connect() as conn:
        release = _current(conn)
        day = _date(request.args.get("date"), release)
        exists = conn.execute("SELECT 1 FROM etf_derived_daily WHERE date=? LIMIT 1", (day,)).fetchone()
        if not exists:
            raise ApiError("NO_DATA", "所选日期没有已计算的龙虎榜", 404)
        base = "FROM etf_derived_daily WHERE date=? AND direction=? AND delta_rank IS NOT NULL"
        result = _paged(conn, f"SELECT * {base} ORDER BY delta_shares {order},code ASC",
                        [day, direction], f"SELECT COUNT(*) {base}", [day, direction], page, size)
        result.update({"release": release, "statistic_date": day,
                       "baseline_date": result["items"][0]["baseline_date"] if result["items"] else None,
                       "rule": "按原始份额增减排序；增加、减少各只保留前100名。"})
        return _success(result)


@mini_api.get("/search")
def search():
    keyword = request.args.get("keyword", "").strip()
    if not keyword or len(keyword) > 24 or not re.fullmatch(r"[0-9A-Za-z\u3400-\u9fff]+", keyword):
        raise ApiError("BAD_REQUEST", "请输1到24个中文、字母或数字")
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with _connect() as conn:
        release = _current(conn)
        items = [dict(row) for row in conn.execute(
            """SELECT f.code,f.name,f.market,f.type AS fund_type,NULLIF(f.theme,'') AS sector,
                      d.delta_shares,d.change_rate,d.price_change_rate,d.quality_status
               FROM funds f LEFT JOIN etf_derived_daily d
                 ON d.code=f.code AND d.date=?
               WHERE f.code LIKE ? ESCAPE '\\' OR f.name LIKE ? ESCAPE '\\'
               ORDER BY CASE WHEN f.code=? THEN 0 ELSE 1 END,f.code LIMIT 20""",
            (release["statistic_date"], f"%{escaped}%", f"%{escaped}%", keyword),
        )]
        return _success({"items": items, "keyword": keyword, "release": release,
                         "note": "" if items else "数据库中没有匹配记录；这不代表市场上不存在该ETF。"})


@mini_api.get("/detail/<code>")
def detail(code: str):
    code = _code(code)
    try:
        period = int(request.args.get("period", 30))
    except ValueError as exc:
        raise ApiError("BAD_REQUEST", "趋势周期无效") from exc
    if period not in {7, 30, 90}:
        raise ApiError("BAD_REQUEST", "趋势周期仅支持7、30、90个交易日")
    with _connect() as conn:
        release = _current(conn)
        day = _date(request.args.get("date"), release)
        master = conn.execute(
            "SELECT code,name,market,type AS fund_type,NULLIF(theme,'') AS sector,issue_year FROM funds WHERE code=?",
            (code,),
        ).fetchone()
        if not master:
            raise ApiError("NO_DATA", "数据库中没有该ETF记录", 404)
        history = [dict(row) for row in conn.execute(
            """WITH recent_dates AS (
                   SELECT DISTINCT date FROM fund_scale WHERE date<=? ORDER BY date DESC LIMIT ?
               )
               SELECT d.date,s.shares,p.close,
                      CASE WHEN s.shares IS NULL THEN 'MISSING' ELSE 'OK' END AS share_status,
                      CASE WHEN p.close IS NULL THEN 'MISSING' ELSE 'OK' END AS price_status
               FROM recent_dates d
               LEFT JOIN fund_scale s ON s.date=d.date AND s.code=?
               LEFT JOIN fund_price p ON p.date=d.date AND p.code=?
               ORDER BY d.date""",
            (day, period, code, code),
        )]
        snapshot = conn.execute(
            "SELECT * FROM etf_derived_daily WHERE date=? AND code=?",
            (day, code),
        ).fetchone()
        item = _row(snapshot)
        return _success({"master": dict(master), "snapshot": item, "history": history,
                         "streak": item, "anomaly": item, "requestedPeriod": period,
                         "availablePoints": len(history), "statistic_date": day, "release": release})


@mini_api.get("/streaks")
def streaks():
    direction = request.args.get("direction", "increase")
    if direction not in {"increase", "decrease"}:
        raise ApiError("BAD_REQUEST", "连续方向无效")
    page, size = _paging()
    with _connect() as conn:
        release = _current(conn)
        day = _date(request.args.get("date"), release)
        base = "FROM etf_derived_daily WHERE date=? AND streak_direction=? AND streak_status='OK' AND streak_days>=2"
        result = _paged(conn, f"SELECT * {base} ORDER BY streak_days DESC,ABS(streak_change_rate) DESC,code",
                        [day, direction], f"SELECT COUNT(*) {base}", [day, direction], page, size)
        result.update({"release": release, "statistic_date": day,
                       "rule": "仅计算当日份额超过100亿份的ETF；份额不变、缺失或未达规模门槛均中断。"})
        return _success(result)


@mini_api.get("/anomalies")
def anomalies():
    page, size = _paging()
    with _connect() as conn:
        release = _current(conn)
        day = _date(request.args.get("date"), release)
        base = "FROM etf_derived_daily WHERE date=? AND anomaly_qualified=1"
        result = _paged(conn, f"SELECT * {base} ORDER BY anomaly_ratio DESC,code",
                        [day], f"SELECT COUNT(*) {base}", [day], page, size)
        result.update({"release": release, "statistic_date": day, "disclaimer": DISCLAIMER,
                       "rule": "当日绝对变化率÷此前20个有效交易日绝对变化率中位数；仅展示当日份额超过100亿份的ETF。"})
        return _success(result)


@mini_api.get("/sectors")
def sectors():
    page, size = _paging()
    with _connect() as conn:
        release = _current(conn)
        day = _date(request.args.get("date"), release)
        base = "FROM etf_sector_daily WHERE date=?"
        result = _paged(conn, f"SELECT * {base} ORDER BY rank,sector",
                        [day], f"SELECT COUNT(*) {base}", [day], page, size)
        result.update({"release": release, "statistic_date": day})
        return _success(result)


@mini_api.get("/sectors/<path:sector>/samples")
def sector_samples(sector: str):
    sector = sector.strip()
    if not sector or len(sector) > 40:
        raise ApiError("BAD_REQUEST", "板块名称无效")
    page, size = _paging()
    with _connect() as conn:
        release = _current(conn)
        day = _date(request.args.get("date"), release)
        base = "FROM etf_derived_daily WHERE date=? AND sector=?"
        result = _paged(conn, f"SELECT * {base} ORDER BY change_rate DESC,code",
                        [day, sector], f"SELECT COUNT(*) {base}", [day, sector], page, size)
        result.update({"release": release, "sector": sector, "statistic_date": day})
        return _success(result)


@mini_api.route("/watchlist", methods=["GET"])
def watchlist():
    with _connect() as conn:
        release = _current(conn)
        items = [dict(row) for row in conn.execute(
            """SELECT w.code,w.observation,w.created_at,w.updated_at,d.*
               FROM web_watchlists w LEFT JOIN etf_derived_daily d
                 ON d.code=w.code AND d.date=?
               WHERE w.user_id=? ORDER BY w.updated_at DESC,w.code""",
            (release["statistic_date"], LOCAL_USER_ID),
        )]
        return _success({"items": items, "release": release})


@mini_api.route("/watchlist/<code>", methods=["POST", "PATCH", "DELETE"])
def mutate_watchlist(code: str):
    code = _code(code)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect(write=True) as conn:
        exists = conn.execute("SELECT 1 FROM funds WHERE code=?", (code,)).fetchone()
        if not exists:
            raise ApiError("NO_DATA", "数据库中没有该ETF记录", 404)
        if request.method == "DELETE":
            with conn:
                conn.execute("DELETE FROM web_watchlists WHERE user_id=? AND code=?", (LOCAL_USER_ID, code))
            return _success({"code": code, "removed": True})
        payload = request.get_json(silent=True) or {}
        observation = int(bool(payload.get("observation", False)))
        with conn:
            conn.execute(
                """INSERT INTO web_watchlists(user_id,code,observation,created_at,updated_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(user_id,code) DO UPDATE SET
                   observation=excluded.observation,updated_at=excluded.updated_at""",
                (LOCAL_USER_ID, code, observation, now, now),
            )
        return _success({"code": code, "added": True, "observation": bool(observation)})


@mini_api.get("/user/status")
def user_status():
    with _connect() as conn:
        row = conn.execute(
            "SELECT tier,status,expires_at,plans_open FROM web_user_entitlements WHERE user_id=?",
            (LOCAL_USER_ID,),
        ).fetchone()
        if not row:
            return _success({"tier": "basic", "membershipActive": False,
                             "expiresAt": None, "source": "local-default", "plansOpen": False})
        return _success({"tier": "member" if row["tier"] == "member" else "basic",
                         "membershipActive": row["status"] == "active",
                         "expiresAt": row["expires_at"], "source": "local-sqlite",
                         "plansOpen": bool(row["plans_open"])})


@mini_api.get("/admin/members")
def admin_members():
    """Local-only membership review inventory; never reaches CloudBase from Web."""
    try:
        return _success(list_members(status=request.args.get("status", ""),
                                     keyword=request.args.get("keyword", "")))
    except ValueError as exc:
        raise ApiError("BAD_REQUEST", str(exc)) from exc


@mini_api.post("/admin/members")
def admin_create_member():
    try:
        return _success(save_member(None, {**(request.get_json(silent=True) or {}), "action": "create"}), 201)
    except ValueError as exc:
        raise ApiError("BAD_REQUEST", str(exc)) from exc


@mini_api.patch("/admin/members/<member_id>")
def admin_update_member(member_id: str):
    try:
        return _success(save_member(member_id, request.get_json(silent=True) or {}))
    except ValueError as exc:
        raise ApiError("BAD_REQUEST", str(exc)) from exc


@mini_api.post("/admin/members/<member_id>/adopt-remote")
def admin_adopt_remote(member_id: str):
    try:
        return _success(adopt_remote(member_id))
    except ValueError as exc:
        raise ApiError("BAD_REQUEST", str(exc)) from exc
