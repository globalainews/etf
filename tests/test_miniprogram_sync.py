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
        "batch": {"_id": "r1", "release_id": "r1", "counts": {name: len(rows) for name, rows in collections.items()}},
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

