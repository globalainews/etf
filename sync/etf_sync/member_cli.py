"""Manual membership review tool for the ETF mini-program.

This intentionally does not try to access Video Accounts membership data.  An
operator verifies the submitted display name in the Video Accounts backend,
then records the actual membership dates here through the server-side API Key.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
from typing import Any

from .config import SyncConfig
from .runner import make_real_client


def _date(value: str, label: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label}须为 YYYY-MM-DD") from exc


def _load(client: Any, request_id: str) -> dict[str, Any]:
    row = client.get_document("user_members", request_id)
    if not row:
        raise RuntimeError("未找到该会员申请；请先执行 --list-pending 并复制 request_id")
    return row


def _safe(row: dict[str, Any]) -> dict[str, Any]:
    """Avoid printing OpenID in routine console output."""
    return {
        "request_id": row.get("_id"),
        "nickname": row.get("nickname"),
        "video_member_name": row.get("video_member_name"),
        "status": row.get("status"),
        "is_member": row.get("is_member", False),
        "member_start_date": row.get("member_start_date"),
        "member_end_date": row.get("member_end_date"),
        "applied_at": row.get("applied_at"),
        "reviewed_at": row.get("reviewed_at"),
        "review_note": row.get("review_note", ""),
    }


def _list_pending(client: Any) -> dict[str, Any]:
    rows = client.find_documents("user_members", {"status": "pending"}, limit=100)
    rows.sort(key=lambda item: str(item.get("applied_at", "")))
    return {"count": len(rows), "items": [_safe(row) for row in rows], "truncated": len(rows) == 100}


def _update(client: Any, request_id: str, *, status: str, is_member: bool,
            start: str | None, end: str | None, note: str) -> dict[str, Any]:
    row = _load(client, request_id)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row.update({
        "status": status,
        "is_member": is_member,
        "member_start_date": start,
        "member_end_date": end,
        "reviewed_at": now,
        "review_note": note[:200],
        "review_source": "operator_cli",
        "updated_at": now,
        "schema_version": "1.0",
    })
    client.upsert_many("user_members", [row], batch_size=1)
    return _safe(row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review ETF mini-program Video Accounts membership applications")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list-pending", action="store_true", help="list pending applications without exposing OpenID")
    group.add_argument("--approve", metavar="REQUEST_ID", help="approve an application after manual Video Accounts verification")
    group.add_argument("--reject", metavar="REQUEST_ID", help="reject an application")
    group.add_argument("--revoke", metavar="REQUEST_ID", help="revoke an existing membership")
    parser.add_argument("--start", help="actual Video Accounts membership start date YYYY-MM-DD; required for --approve")
    parser.add_argument("--end", help="actual Video Accounts membership end date YYYY-MM-DD; required for --approve")
    parser.add_argument("--note", default="", help="short operator note (max 200 characters)")
    args = parser.parse_args(argv)
    if len(args.note) > 200:
        parser.error("--note不能超过200个字符")
    client = make_real_client(SyncConfig.from_env())
    if args.list_pending:
        print(json.dumps(_list_pending(client), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.approve:
        if not args.start or not args.end:
            parser.error("--approve必须同时提供--start和--end")
        start = _date(args.start, "开始日期")
        end = _date(args.end, "结束日期")
        if end < start:
            parser.error("--end不能早于--start")
        result = _update(client, args.approve, status="active", is_member=True, start=start, end=end, note=args.note)
    elif args.reject:
        result = _update(client, args.reject, status="rejected", is_member=False, start=None, end=None, note=args.note)
    else:
        result = _update(client, args.revoke, status="revoked", is_member=False, start=None, end=None, note=args.note)
    print(json.dumps({"ok": True, "member": result}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
