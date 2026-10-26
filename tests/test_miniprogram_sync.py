from __future__ import annotations

import pytest
from urllib import error

from sync.etf_sync.client import CloudbaseError, HttpCloudbaseClient, MemoryCloudbaseClient
from sync.etf_sync.runner import sync_bundle


def bundle() -> dict:
    release = {"_id": "current", "release_id": "r1", "release_seq": 1, "statistic_date": "2026-01-02", "baseline_date": "2026-01-01", "quality_status": "OK", "coverage": {}}
    collections = {
        "etf_master": [{"_id": "510001", "code": "510001"}],
        "etf_history": [{"_id": "h1", "release_id": "r1", "date": "2026-01-02", "code": "510001"}],
        "ranking_snapshots": [{"_id": "r1_a", "release_id": "r1", "code": "510001"}],
        "streak_snapshots": [{"_id": "r1_a", "release_id": "r1", "code": "510001"}],
        "anomaly_snapshots": [{"_id": "r1_a", "release_id": "r1", "code": "510001"}],
        "sector_snapshots": [{"_id": "r1_a", "release_id": "r1"}],
    }
    return {
        "release": release,
        "batch": {"_id": "r1", "release_id": "r1", "release_seq": 1,
                  "counts": {name: len(rows) for name, rows in collections.items()}},
        "collections": collections,
    }


def test_sync_is_idempotent():
    client = MemoryCloudbaseClient()
    sync_bundle(client, bundle())
    sync_bundle(client, bundle())
    assert len(client.data["ranking_snapshots"]) == 1
    assert client.data["publish_status"]["current"]["release_id"] == "r1"


def test_partial_failure_does_not_publish_pointer():
    client = MemoryCloudbaseClient(fail_collection="anomaly_snapshots")
    with pytest.raises(CloudbaseError):
        sync_bundle(client, bundle())
    assert "current" not in client.data.get("publish_status", {})
    assert client.data["sync_batches"]["r1"]["state"] == "failed"


def test_network_retry_is_finite(monkeypatch):
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(1)
        raise error.URLError("offline")

    monkeypatch.setattr("sync.etf_sync.client.request.urlopen", fail)
    monkeypatch.setattr("sync.etf_sync.client.time.sleep", lambda _: None)
    client = HttpCloudbaseClient("env-test", "secret-not-logged", max_retries=3)
    with pytest.raises(CloudbaseError, match="network error") as exc:
        client.get_document("x", "y")
    assert len(attempts) == 3
    assert "secret-not-logged" not in str(exc.value)


def test_transaction_id_is_query_parameter_and_transaction_calls_do_not_retry(monkeypatch):
    calls = []

    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    client = HttpCloudbaseClient("env-test", "secret-not-logged", max_retries=3)
    monkeypatch.setattr(client, "_request", capture)
    client.transaction_upsert("tx-1", "publish_status", {"_id": "current"})
    client.commit("tx-1")
    client.rollback("tx-1")

    upsert = calls[0]
    assert upsert[1]["query"] == {"transactionId": "tx-1"}
    assert upsert[1]["max_attempts"] == 1
    assert "transactionId" not in upsert[0][2]
    assert calls[1][1]["max_attempts"] == 1
    assert calls[2][1]["max_attempts"] == 1


def test_rollback_failure_does_not_hide_publish_failure():
    class BrokenTransactionClient(MemoryCloudbaseClient):
        def commit(self, transaction_id: str) -> None:
            raise CloudbaseError("commit failed")

        def rollback(self, transaction_id: str) -> None:
            raise CloudbaseError("rollback expired")

    with pytest.raises(CloudbaseError, match="commit failed"):
        sync_bundle(BrokenTransactionClient(), bundle())


def test_resume_publish_validates_counts_without_reuploading_business_data():
    class TrackingClient(MemoryCloudbaseClient):
        def __init__(self):
            super().__init__()
            self.writes = []

        def upsert_many(self, collection, documents, *, batch_size=80):
            docs = list(documents)
            self.writes.append((collection, len(docs)))
            return super().upsert_many(collection, docs, batch_size=batch_size)

    payload = bundle()
    client = TrackingClient()
    for collection, documents in payload["collections"].items():
        MemoryCloudbaseClient.upsert_many(client, collection, documents)
    MemoryCloudbaseClient.upsert_many(
        client, "sync_batches", [{**payload["batch"], "state": "failed"}]
    )

    result = sync_bundle(client, payload, resume_publish=True)

    assert result["published"] is True
    assert result["resumed"] is True
    assert all(result["written"][name] == 0 for name in payload["collections"])
    assert not any(name in payload["collections"] for name, _ in client.writes)
    assert client.data["publish_status"]["current"]["release_id"] == "r1"


def test_resume_publish_refuses_incomplete_remote_batch():
    payload = bundle()
    client = MemoryCloudbaseClient()
    for collection, documents in payload["collections"].items():
        if collection != "etf_history":
            client.upsert_many(collection, documents)
    client.upsert_many("sync_batches", [{**payload["batch"], "state": "failed"}])

    with pytest.raises(RuntimeError, match="remote count mismatch"):
        sync_bundle(client, payload, resume_publish=True)
    assert "current" not in client.data.get("publish_status", {})

