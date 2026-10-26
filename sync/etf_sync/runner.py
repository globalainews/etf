from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
import time
import uuid

from .client import HttpCloudbaseClient
from .config import SyncConfig
from .published_bundle import build_published_bundle


COLLECTIONS = (
    "etf_master", "etf_history", "ranking_snapshots", "streak_snapshots",
    "anomaly_snapshots", "sector_snapshots", "sync_batches", "publish_status",
    "user_watchlists", "user_entitlements", "sync_locks",
)


def summarize(bundle: dict[str, Any]) -> dict[str, Any]:
    dates = [row.get("date") for row in bundle["collections"]["etf_history"] if row.get("date")]
    return {
        "release_id": bundle["release"]["release_id"],
        "release_seq": bundle["release"]["release_seq"],
        "statistic_date": bundle["release"]["statistic_date"],
        "baseline_date": bundle["release"].get("baseline_date"),
        "history_date_range": [min(dates), max(dates)] if dates else [],
        "counts": bundle["batch"]["counts"],
        "quality_status": bundle["release"]["quality_status"],
        "coverage": bundle["release"]["coverage"],
    }


def sync_bundle(
    client: Any,
    bundle: dict[str, Any],
    *,
    batch_size: int = 80,
    resume_publish: bool = False,
) -> dict[str, Any]:
    release_id = bundle["release"]["release_id"]
    master_codes = {row["code"] for row in bundle["collections"]["etf_master"]}
    if master_codes and any(row.get("code") not in master_codes for row in bundle["collections"]["ranking_snapshots"]):
        raise ValueError("ranking snapshot contains code missing from etf_master")
    if any(len(row.get("code", "")) != 6 or not row["code"].isdigit() for row in bundle["collections"]["etf_master"]):
        raise ValueError("etf_master contains invalid ETF code")
    remote_batch = None
    untracked_recovery = False
    if resume_publish:
        current = client.get_document("publish_status", "current")
        remote_batch = client.get_document("sync_batches", release_id)
        if (current and current.get("release_id") == release_id and remote_batch
                and remote_batch.get("state") == "published"):
            return {**summarize(bundle), "written": {}, "published": True,
                    "resumed": True, "already_published": True}
        if remote_batch:
            if remote_batch.get("state") not in {"failed", "uploading"}:
                raise RuntimeError("remote batch is not eligible for publish-only recovery")
            if int(remote_batch.get("release_seq", -1)) != int(bundle["release"]["release_seq"]):
                raise RuntimeError("remote failed batch release sequence does not match the recovery bundle")
            if remote_batch.get("counts") != bundle["batch"]["counts"]:
                raise RuntimeError("remote failed batch counts do not match the local recovery bundle")
        else:
            # Older failed clients could finish document writes but fail before
            # recording sync_batches.  Exact per-release counts below are the
            # safety gate before this path can publish a pointer.
            untracked_recovery = True
    else:
        for name in COLLECTIONS:
            client.ensure_collection(name)
    owner = f"{release_id}:{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    existing_lock = client.get_document("sync_locks", "current")
    if existing_lock and existing_lock.get("owner") and existing_lock.get("expires_at", "") > now.isoformat():
        raise RuntimeError("another synchronization process holds the publish lease")
    lock = {"_id": "current", "owner": owner, "release_id": release_id,
            "acquired_at": now.isoformat(timespec="seconds"),
            "expires_at": (now + timedelta(minutes=30)).isoformat(timespec="seconds")}
    client.upsert_many("sync_locks", [lock], batch_size=1)
    if (client.get_document("sync_locks", "current") or {}).get("owner") != owner:
        raise RuntimeError("failed to acquire publish lease")
    if not resume_publish:
        batch = {**bundle["batch"], "state": "uploading"}
        client.upsert_many("sync_batches", [batch], batch_size=batch_size)
    written: dict[str, int] = {}
    try:
        if resume_publish:
            written = {collection: 0 for collection in bundle["collections"]}
        else:
            for collection, documents in bundle["collections"].items():
                written[collection] = client.upsert_many(collection, documents, batch_size=batch_size)
        expected = bundle["batch"]["counts"]
        for collection in ("etf_history", "ranking_snapshots", "streak_snapshots", "anomaly_snapshots", "sector_snapshots"):
            actual = client.count(collection, {"release_id": release_id})
            if actual != expected[collection]:
                raise RuntimeError(f"{collection} remote count mismatch: {actual} != {expected[collection]}")
        minimum_master_count = int(bundle["release"].get("coverage", {}).get("fund_total", expected["etf_master"]))
        if client.count("etf_master", {}) < minimum_master_count:
            raise RuntimeError("etf_master remote count is smaller than the prepared release")
        if (client.get_document("sync_locks", "current") or {}).get("owner") != owner:
            raise RuntimeError("publish lease was lost before pointer switch")
        published_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ready_batch = {**(remote_batch or bundle["batch"]), "state": "published",
                       "published_at": published_at}
        ready_batch.pop("failure_type", None)
        if untracked_recovery:
            ready_batch["recovery_mode"] = "count_verified_existing_release"
            ready_batch["business_documents_reused"] = True
        current = {**bundle["release"], "published_at": published_at}
        tx = client.start_transaction()
        try:
            client.transaction_upsert(tx, "sync_batches", ready_batch)
            client.transaction_upsert(tx, "publish_status", current)
            client.commit(tx)
        except Exception:
            # A timed-out transaction has already disappeared server-side.
            # Keep the original failure (and its request id) instead of
            # replacing it with the inevitable rollback error.
            try:
                client.rollback(tx)
            except Exception:
                pass
            raise
        return {**summarize(bundle), "written": written, "published": True,
                "resumed": resume_publish}
    except Exception as exc:
        failed = {**bundle["batch"], "state": "failed", "failure_type": type(exc).__name__}
        client.upsert_many("sync_batches", [failed], batch_size=1)
        raise
    finally:
        active = client.get_document("sync_locks", "current")
        if active and active.get("owner") == owner:
            client.upsert_many("sync_locks", [{**active, "owner": None,
                "released_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}], batch_size=1)


def prepare_bundle(
    config: SyncConfig,
    client: Any | None,
    *,
    initial: bool,
    correction_from: str | None,
    resume_publish: bool = False,
) -> dict[str, Any]:
    current = client.get_document("publish_status", "current") if client else None
    if resume_publish:
        if client is None:
            raise ValueError("publish-only recovery requires a real CloudBase client")
        probe = build_published_bundle(config, release_seq=0, initial=initial,
                                       correction_from=correction_from)
        remote_batch = client.get_document("sync_batches", probe["release"]["release_id"])
        if remote_batch and "release_seq" in remote_batch:
            return build_published_bundle(
                config,
                release_seq=int(remote_batch["release_seq"]),
                initial=initial,
                correction_from=correction_from,
            )
        # No batch marker: use a fresh high-water sequence so already-uploaded
        # history revisions remain visible to the pointer after count checks.
        return build_published_bundle(
            config,
            release_seq=int(time.time() * 1000),
            initial=initial,
            correction_from=correction_from,
        )
    release_seq = max(int((current or {}).get("release_seq", 0)) + 1, int(time.time() * 1000))
    bundle = build_published_bundle(config, release_seq=release_seq, initial=initial, correction_from=correction_from)
    if current and current.get("release_id") == bundle["release"]["release_id"]:
        bundle = build_published_bundle(config, release_seq=int(current["release_seq"]), initial=initial, correction_from=correction_from)
    if current and not initial and current.get("master_hash") == bundle["release"].get("master_hash"):
        bundle["collections"]["etf_master"] = []
        bundle["batch"]["counts"]["etf_master"] = 0
        bundle["batch"]["master_reused"] = True
    return bundle


def export_bundle(bundle: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def make_real_client(config: SyncConfig) -> HttpCloudbaseClient:
    return HttpCloudbaseClient(config.env_id, config.api_key, timeout=config.timeout_seconds, max_retries=config.max_retries)

