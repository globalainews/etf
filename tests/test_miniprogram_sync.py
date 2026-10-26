from __future__ import annotations

import pytest
from urllib import error

from sync.etf_sync.client import (CloudbaseError, HttpCloudbaseClient,
                                  MemoryCloudbaseClient, _decode_ejson)
from sync.etf_sync.runner import diagnose_remote_bundle, sync_bundle, write_probe_bundle


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


def test_cloudbase_count_uses_official_filtered_count_endpoint(monkeypatch):
    calls = []

    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return {"list": [[{"n": 7}]]}

    client = HttpCloudbaseClient("env-test", "secret-not-logged")
    monkeypatch.setattr(client, "_request", capture)
    assert client.count("etf_history", {"release_id": "r1"}) == 7
    assert calls[0][0][:2] == ("POST", "/commands")
    assert calls[0][0][2] == {"commands": [{"count": "etf_history", "query": {"release_id": "r1"}}]}


def test_strict_ejson_numbers_are_decoded():
    assert _decode_ejson({"n": {"$numberLong": "12"}, "items": [{"$numberInt": "3"}]}) == {
        "n": 12, "items": [3],
    }


def test_first_document_reads_the_standard_list_response(monkeypatch):
    client = HttpCloudbaseClient("env-test", "secret-not-logged")
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: {"list": [[{"_id": "a"}]]})
    assert client.first_document("etf_history") == {"_id": "a"}


def test_upsert_rejects_silent_or_partial_command_results(monkeypatch):
    client = HttpCloudbaseClient("env-test", "secret-not-logged")
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: {"list": [[{"n": 0, "ok": 1}]]})
    with pytest.raises(CloudbaseError, match="acknowledged 0 of 1"):
        client.upsert_many("etf_history", [{"_id": "one"}])


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


def test_transactional_publish_documents_use_one_run_commands_request(monkeypatch):
    calls = []

    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return {"list": [[{"n": 1, "ok": 1}], [{"n": 1, "ok": 1}]]}

    client = HttpCloudbaseClient("env-test", "secret-not-logged", max_retries=3)
    monkeypatch.setattr(client, "_request", capture)
    client.transaction_upsert_many("tx-1", [
        ("sync_batches", {"_id": "r1", "state": "published"}),
        ("publish_status", {"_id": "current", "release_id": "r1"}),
    ])

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:2] == ("POST", "/commands")
    assert args[2]["transactionId"] == "tx-1"
    assert [command["update"] for command in args[2]["commands"]] == ["sync_batches", "publish_status"]
    assert kwargs["max_attempts"] == 1


def test_pointer_write_failure_keeps_release_unpublished():
    class BrokenPointerClient(MemoryCloudbaseClient):
        def upsert_many(self, collection, documents, *, batch_size=80):
            if collection == "publish_status":
                raise CloudbaseError("pointer write failed")
            return super().upsert_many(collection, documents, batch_size=batch_size)

    client = BrokenPointerClient()
    with pytest.raises(CloudbaseError, match="pointer write failed"):
        sync_bundle(client, bundle())
    assert "current" not in client.data.get("publish_status", {})
    assert client.data["sync_batches"]["r1"]["state"] == "failed"


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

    result = sync_bundle(client, payload, resume_publish=True, publish_verified=True)

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

    with pytest.raises(RuntimeError, match="automatic publish recovery"):
        sync_bundle(client, payload, resume_publish=True)
    assert "current" not in client.data.get("publish_status", {})


def test_resume_publish_without_batch_marker_requires_manual_console_verification():
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

    with pytest.raises(RuntimeError, match="automatic publish recovery"):
        sync_bundle(client, payload, resume_publish=True)
    assert not any(name in payload["collections"] for name, _ in client.writes)


def test_cloud_diagnostic_is_read_only_and_reports_release_counts():
    payload = bundle()
    client = MemoryCloudbaseClient()
    for collection, documents in payload["collections"].items():
        client.upsert_many(collection, documents)

    report = diagnose_remote_bundle(client, payload, env_id="cloud1-test")

    assert report["read_only"] is True
    assert report["env_id"] == "cloud1-test"
    assert report["collections"]["etf_history"] == {
        "total": 1, "for_local_release": 1, "expected_for_local_release": 1,
    }
    assert report["sample_documents"]["ranking_snapshots"]["release_id"] == "r1"
    assert report["sample_documents"]["ranking_snapshots:first"]["found"] is True
    assert report["sample_documents"]["ranking_snapshots:release_match"]["found"] is True


def test_normal_sync_uses_server_write_acknowledgements_without_collection_scans():
    class NoCountClient(MemoryCloudbaseClient):
        def count(self, collection, query):
            raise AssertionError("normal upload must not use incompatible collection count endpoints")

    result = sync_bundle(NoCountClient(), bundle())
    assert result["published"] is True
    assert result["validation_method"] == "all-upload-batches-server-acknowledged"


def test_explicit_write_probe_writes_one_document_per_collection():
    client = MemoryCloudbaseClient()
    result = write_probe_bundle(client, bundle())
    assert result["ok"] is True
    assert set(result["probed"]) == {"etf_master", "etf_history", "ranking_snapshots",
                                      "streak_snapshots", "anomaly_snapshots", "sector_snapshots"}


def test_manually_verified_publish_does_not_query_or_rewrite_business_documents():
    class VerifiedClient(MemoryCloudbaseClient):
        def count(self, collection, query):
            raise AssertionError("verified publish must not count business documents")

    payload = bundle()
    client = VerifiedClient()
    client.upsert_many("sync_batches", [{**payload["batch"], "state": "failed"}])

    result = sync_bundle(client, payload, resume_publish=True, publish_verified=True)

    assert result["published"] is True
    assert result["publish_verified"] is True
    assert client.data["sync_batches"]["r1"]["publish_verification"] == "operator-confirmed-cloudbase-console"

