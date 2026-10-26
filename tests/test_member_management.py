from __future__ import annotations

import sqlite3
from pathlib import Path

import db
from sync.etf_sync.client import MemoryCloudbaseClient
from sync.etf_sync.member_store import adopt_remote, list_members, pull_members, push_members, save_member


def _new_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    db.ensure_schema(conn)
    conn.close()


def _remote(member_id: str = "a" * 64, *, nickname: str = "云端用户", status: str = "pending") -> dict:
    return {"_id": member_id, "openid": "openid_12345678", "nickname": nickname,
            "video_member_name": "Global会员", "status": status, "is_member": status == "active",
            "member_start_date": "2026-10-01" if status == "active" else None,
            "member_end_date": "2026-11-01" if status == "active" else None,
            "applied_at": "2026-10-20T00:00:00+00:00", "updated_at": "2026-10-20T00:00:00+00:00"}


def test_pull_review_push_and_conflict_protection(tmp_path: Path):
    path = tmp_path / "etf.db"
    _new_db(path)
    cloud = MemoryCloudbaseClient()
    cloud.upsert_many("user_members", [_remote()])
    assert pull_members(cloud, db_path=str(path))["imported"] == 1
    before = list_members(db_path=str(path))
    member_id = before["items"][0]["member_id"]
    approved = save_member(member_id, {"action": "approve", "nickname": "云端用户",
        "videoMemberName": "Global会员", "memberStartDate": "2026-10-20", "memberEndDate": "2026-11-20"}, db_path=str(path))
    assert approved["dirty"] == 1 and approved["status"] == "active"
    assert push_members(cloud, db_path=str(path))["pushed"] == 1
    assert cloud.get_document("user_members", member_id)["status"] == "active"
    save_member(member_id, {"action": "revoke", "nickname": "云端用户", "videoMemberName": "Global会员"}, db_path=str(path))
    cloud.upsert_many("user_members", [{**cloud.get_document("user_members", member_id), "nickname": "外部修改"}])
    result = push_members(cloud, db_path=str(path))
    assert result["conflicts"] == 1
    assert cloud.get_document("user_members", member_id)["status"] == "active"
    adopted = adopt_remote(member_id, db_path=str(path))
    assert adopted["nickname"] == "外部修改" and adopted["dirty"] == 0


def test_manual_member_requires_trusted_openid_and_dates(tmp_path: Path):
    path = tmp_path / "etf.db"
    _new_db(path)
    try:
        save_member(None, {"action": "create", "memberId": "b" * 64, "openid": "bad",
                            "nickname": "用户", "videoMemberName": "会员"}, db_path=str(path))
    except ValueError as exc:
        assert "OpenID" in str(exc)
    else:
        raise AssertionError("invalid OpenID should be rejected")
    item = save_member(None, {"action": "create", "memberId": "b" * 64, "openid": "openid_87654321",
                              "nickname": "用户", "videoMemberName": "会员", "memberStartDate": "2026-10-01",
                              "memberEndDate": "2026-11-01"}, db_path=str(path))
    assert item["status"] == "active" and item["dirty"] == 1
