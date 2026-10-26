"""Local membership mirror and conflict-safe CloudBase synchronisation."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
from typing import Any

import db

MEMBER_STATES = {"pending", "active", "rejected", "revoked"}
OPENID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _date(value: Any, label: str, *, required: bool = False) -> str | None:
    if value in (None, ""):
        if required:
            raise ValueError(f"请填写{label}")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label}须为 YYYY-MM-DD")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError(f"{label}须为 YYYY-MM-DD") from exc


def _text(value: Any, label: str, maximum: int, *, required: bool = True) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError(f"请填写{label}")
    if len(result) > maximum or any(ord(char) < 32 for char in result):
        raise ValueError(f"{label}格式无效")
    return result


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        if "$date" in value and len(value) == 1:
            return str(value["$date"])
        return {key: _normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def fingerprint(document: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(_normalize(document), ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _connect(path: str | None = None) -> sqlite3.Connection:
    conn = db.connect(path or str(db.DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    openid = item.pop("openid", "")
    item["openid_masked"] = f"{openid[:4]}…{openid[-4:]}" if len(openid) > 8 else "已保存"
    item.pop("remote_json", None)
    item.pop("remote_fingerprint", None)
    return item


def _audit(conn: sqlite3.Connection, member_id: str, action: str, detail: dict[str, Any] | None = None) -> None:
    conn.execute("INSERT INTO web_member_audit(member_id,action,detail_json,created_at) VALUES(?,?,?,?)",
                 (member_id, action, json.dumps(detail or {}, ensure_ascii=False, sort_keys=True), utcnow()))


def _record_from_remote(remote: dict[str, Any], *, now: str) -> dict[str, Any]:
    member_id = _text(remote.get("_id"), "云端会员ID", 128)
    openid = _text(remote.get("openid"), "OpenID", 128)
    status = str(remote.get("status") or "pending")
    if status not in MEMBER_STATES:
        status = "pending"
    return {
        "member_id": member_id, "openid": openid,
        "nickname": _text(remote.get("nickname"), "微信昵称", 32, required=False),
        "video_member_name": _text(remote.get("video_member_name"), "视频号会员名称", 40, required=False),
        "status": status, "is_member": int(remote.get("is_member") is True),
        "member_start_date": _date(remote.get("member_start_date"), "会员开始日期"),
        "member_end_date": _date(remote.get("member_end_date"), "会员结束日期"),
        "applied_at": str(_normalize(remote.get("applied_at")) or ""),
        "reviewed_at": str(_normalize(remote.get("reviewed_at")) or ""),
        "review_note": _text(remote.get("review_note"), "审核备注", 200, required=False),
        "review_source": _text(remote.get("review_source"), "审核来源", 64, required=False),
        "remote_fingerprint": fingerprint(remote),
        "remote_json": json.dumps(_normalize(remote), ensure_ascii=False, sort_keys=True, default=str),
        "last_pulled_at": now, "updated_at": now,
    }


def pull_members(client: Any, *, db_path: str | None = None) -> dict[str, int]:
    remote_rows: list[dict[str, Any]] = []
    for skip in range(0, 10_000, 100):
        page = client.find_documents("user_members", {}, limit=100, skip=skip)
        remote_rows.extend(page)
        if len(page) < 100:
            break
    now = utcnow()
    imported = updated = conflicts = missing_remote = 0
    with _connect(db_path) as conn, conn:
        seen: set[str] = set()
        for remote in remote_rows:
            record = _record_from_remote(remote, now=now)
            member_id = record["member_id"]
            seen.add(member_id)
            local = conn.execute("SELECT * FROM web_member_records WHERE member_id=?", (member_id,)).fetchone()
            if not local:
                cols = list(record) + ["dirty", "conflict", "is_manual", "last_pushed_at", "created_at"]
                values = [record[key] for key in record] + [0, 0, 0, None, now]
                conn.execute(f"INSERT INTO web_member_records({','.join(cols)}) VALUES({','.join('?' for _ in cols)})", values)
                _audit(conn, member_id, "pulled_new")
                imported += 1
            elif local["dirty"] and local["remote_fingerprint"] != record["remote_fingerprint"]:
                conn.execute("UPDATE web_member_records SET conflict=1,remote_json=?,last_pulled_at=?,updated_at=? WHERE member_id=?",
                             (record["remote_json"], now, now, member_id))
                _audit(conn, member_id, "pull_conflict")
                conflicts += 1
            elif not local["dirty"]:
                columns = list(record)
                conn.execute(f"UPDATE web_member_records SET {','.join(f'{key}=?' for key in columns)},dirty=0,conflict=0,is_manual=0,last_pushed_at=NULL WHERE member_id=?",
                             [record[key] for key in columns] + [member_id])
                _audit(conn, member_id, "pulled_update")
                updated += 1
        for local in conn.execute("SELECT member_id FROM web_member_records WHERE is_manual=0 AND dirty=0").fetchall():
            if local["member_id"] not in seen:
                conn.execute("UPDATE web_member_records SET conflict=1,last_pulled_at=?,updated_at=? WHERE member_id=?",
                             (now, now, local["member_id"]))
                _audit(conn, local["member_id"], "pull_missing_remote")
                missing_remote += 1
        conn.execute("INSERT INTO web_member_sync_state(id,last_pull_at,last_result_json) VALUES('current',?,?) ON CONFLICT(id) DO UPDATE SET last_pull_at=excluded.last_pull_at,last_result_json=excluded.last_result_json",
                     (now, json.dumps({"operation": "pull", "remote": len(remote_rows), "imported": imported,
                                       "updated": updated, "conflicts": conflicts, "missing_remote": missing_remote}, ensure_ascii=False)))
    return {"remote": len(remote_rows), "imported": imported, "updated": updated,
            "conflicts": conflicts, "missing_remote": missing_remote}


def _cloud_document(row: sqlite3.Row, now: str) -> dict[str, Any]:
    return {"_id": row["member_id"], "openid": row["openid"], "nickname": row["nickname"],
            "video_member_name": row["video_member_name"], "status": row["status"],
            "is_member": bool(row["is_member"]), "member_start_date": row["member_start_date"],
            "member_end_date": row["member_end_date"], "applied_at": row["applied_at"] or now,
            "reviewed_at": row["reviewed_at"] or None, "review_note": row["review_note"],
            "review_source": row["review_source"] or "local_web", "updated_at": now, "schema_version": "1.0"}


def push_members(client: Any, *, db_path: str | None = None) -> dict[str, int]:
    pushed = conflicts = 0
    now = utcnow()
    with _connect(db_path) as conn, conn:
        rows = conn.execute("SELECT * FROM web_member_records WHERE dirty=1 AND conflict=0 ORDER BY updated_at,member_id").fetchall()
        for row in rows:
            remote = client.get_document("user_members", row["member_id"])
            if row["remote_fingerprint"] and (not remote or fingerprint(remote) != row["remote_fingerprint"]):
                conn.execute("UPDATE web_member_records SET conflict=1,remote_json=?,updated_at=? WHERE member_id=?",
                             (json.dumps(_normalize(remote), ensure_ascii=False, sort_keys=True, default=str) if remote else None, now, row["member_id"]))
                _audit(conn, row["member_id"], "push_conflict")
                conflicts += 1
                continue
            document = _cloud_document(row, now)
            client.upsert_many("user_members", [document], batch_size=1)
            stored = client.get_document("user_members", row["member_id"]) or document
            conn.execute("UPDATE web_member_records SET remote_fingerprint=?,remote_json=?,dirty=0,conflict=0,is_manual=0,last_pushed_at=?,updated_at=? WHERE member_id=?",
                         (fingerprint(stored), json.dumps(_normalize(stored), ensure_ascii=False, sort_keys=True, default=str), now, now, row["member_id"]))
            _audit(conn, row["member_id"], "pushed")
            pushed += 1
        conn.execute("INSERT INTO web_member_sync_state(id,last_push_at,last_result_json) VALUES('current',?,?) ON CONFLICT(id) DO UPDATE SET last_push_at=excluded.last_push_at,last_result_json=excluded.last_result_json",
                     (now, json.dumps({"operation": "push", "pushed": pushed, "conflicts": conflicts}, ensure_ascii=False)))
    return {"pending": len(rows), "pushed": pushed, "conflicts": conflicts}


def list_members(*, status: str = "", keyword: str = "", db_path: str | None = None) -> dict[str, Any]:
    if status and status not in MEMBER_STATES | {"conflict", "dirty", "expired"}:
        raise ValueError("会员状态筛选无效")
    keyword = _text(keyword, "搜索词", 40, required=False)
    clauses, values = [], []
    if status in MEMBER_STATES:
        clauses.append("status=?"); values.append(status)
    elif status == "conflict": clauses.append("conflict=1")
    elif status == "dirty": clauses.append("dirty=1")
    elif status == "expired": clauses.append("status='active' AND member_end_date<?"); values.append(datetime.now().date().isoformat())
    if keyword:
        clauses.append("(nickname LIKE ? OR video_member_name LIKE ?)"); values.extend([f"%{keyword}%", f"%{keyword}%"])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(db_path) as conn:
        rows = conn.execute(f"SELECT * FROM web_member_records {where} ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,updated_at DESC LIMIT 200", values).fetchall()
        count = lambda clause: conn.execute(f"SELECT COUNT(*) FROM web_member_records WHERE {clause}").fetchone()[0]
        state = conn.execute("SELECT * FROM web_member_sync_state WHERE id='current'").fetchone()
        return {"items": [_public(row) for row in rows], "stats": {"pending": count("status='pending'"),
                "active": count("status='active' AND is_member=1 AND (member_end_date IS NULL OR member_end_date>=date('now'))"),
                "inactive": count("status!='active' OR is_member=0 OR (member_end_date IS NOT NULL AND member_end_date<date('now'))"),
                "dirty": count("dirty=1"), "conflict": count("conflict=1")},
                "sync": dict(state) if state else {"last_pull_at": None, "last_push_at": None}}


def save_member(member_id: str | None, payload: dict[str, Any], *, db_path: str | None = None) -> dict[str, Any]:
    action = str(payload.get("action") or "update")
    if action not in {"approve", "reject", "revoke", "update", "create"}:
        raise ValueError("会员操作无效")
    now = utcnow()
    with _connect(db_path) as conn, conn:
        if action == "create":
            openid = _text(payload.get("openid"), "可信OpenID", 128)
            if not OPENID_RE.fullmatch(openid): raise ValueError("可信OpenID格式无效")
            member_id = _text(payload.get("memberId"), "云端会员ID", 128)
            if conn.execute("SELECT 1 FROM web_member_records WHERE member_id=? OR openid=?", (member_id, openid)).fetchone():
                raise ValueError("该会员ID或OpenID已存在")
            base = {"member_id": member_id, "openid": openid, "applied_at": now, "is_manual": 1,
                    "created_at": now, "remote_fingerprint": None, "remote_json": None, "last_pulled_at": None, "last_pushed_at": None}
        else:
            if not member_id: raise ValueError("会员ID缺失")
            row = conn.execute("SELECT * FROM web_member_records WHERE member_id=?", (member_id,)).fetchone()
            if not row: raise ValueError("未找到该会员记录")
            base = dict(row)
        nickname = _text(payload.get("nickname", base.get("nickname")), "微信昵称", 32)
        video = _text(payload.get("videoMemberName", base.get("video_member_name")), "视频号会员名称", 40)
        note = _text(payload.get("reviewNote", base.get("review_note")), "审核备注", 200, required=False)
        status = {"approve": "active", "reject": "rejected", "revoke": "revoked", "create": "active"}.get(action, str(payload.get("status", base.get("status", "pending"))))
        if status not in MEMBER_STATES: raise ValueError("会员状态无效")
        start = _date(payload.get("memberStartDate", base.get("member_start_date")), "会员开始日期", required=status == "active")
        end = _date(payload.get("memberEndDate", base.get("member_end_date")), "会员结束日期", required=status == "active")
        if start and end and end < start: raise ValueError("会员结束日期不能早于开始日期")
        values = {**base, "nickname": nickname, "video_member_name": video, "status": status,
                  "is_member": int(status == "active"), "member_start_date": start, "member_end_date": end,
                  "reviewed_at": now if action in {"approve", "reject", "revoke"} else base.get("reviewed_at"),
                  "review_note": note, "review_source": "local_web", "dirty": 1, "conflict": 0, "updated_at": now}
        columns = ["nickname","video_member_name","status","is_member","member_start_date","member_end_date","reviewed_at","review_note","review_source","dirty","conflict","updated_at"]
        if action == "create":
            columns = list(values)
            conn.execute(f"INSERT INTO web_member_records({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", [values[key] for key in columns])
        else:
            conn.execute(f"UPDATE web_member_records SET {','.join(f'{key}=?' for key in columns)} WHERE member_id=?", [values[key] for key in columns] + [member_id])
        _audit(conn, str(member_id), f"local_{action}")
        row = conn.execute("SELECT * FROM web_member_records WHERE member_id=?", (member_id,)).fetchone()
        return _public(row)


def adopt_remote(member_id: str, *, db_path: str | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn, conn:
        row = conn.execute("SELECT remote_json FROM web_member_records WHERE member_id=?", (member_id,)).fetchone()
        if not row or not row["remote_json"]: raise ValueError("没有可采用的云端版本")
        remote = json.loads(row["remote_json"])
        record = _record_from_remote(remote, now=utcnow())
        cols = list(record)
        conn.execute(f"UPDATE web_member_records SET {','.join(f'{key}=?' for key in cols)},dirty=0,conflict=0,is_manual=0 WHERE member_id=?", [record[key] for key in cols] + [member_id])
        _audit(conn, member_id, "adopted_remote")
        return _public(conn.execute("SELECT * FROM web_member_records WHERE member_id=?", (member_id,)).fetchone())
