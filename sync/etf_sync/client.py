from __future__ import annotations

import json
import random
import time
from typing import Any, Iterable
from urllib import error, parse, request


class CloudbaseError(RuntimeError):
    pass


def _decode_ejson(value: Any) -> Any:
    """Decode the strict EJSON numeric wrappers returned by CloudBase REST."""
    if isinstance(value, list):
        return [_decode_ejson(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"$numberInt"} or set(value) == {"$numberLong"}:
        return int(next(iter(value.values())))
    if set(value) == {"$numberDouble"}:
        return float(value["$numberDouble"])
    if set(value) == {"$numberDecimal"}:
        # Decimal values are only carried through to JSON-facing callers here.
        return value["$numberDecimal"]
    return {key: _decode_ejson(item) for key, item in value.items()}


class HttpCloudbaseClient:
    """Official CloudBase NoSQL HTTP API client using a server-side API Key."""

    def __init__(self, env_id: str, api_key: str, *, timeout: float = 20, max_retries: int = 3):
        if not env_id or not api_key:
            raise ValueError("CLOUDBASE_ENV_ID and CLOUDBASE_API_KEY are required")
        self.base = f"https://{env_id}.api.tcloudbasegateway.com/v1/database/instances/(default)/databases/(default)"
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        query: dict | None = None,
        *,
        max_attempts: int | None = None,
    ) -> Any:
        url = self.base + path
        if query:
            url += "?" + parse.urlencode(query)
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        retryable = {408, 422, 429, 500, 502, 503, 504}
        # A transaction is short lived on CloudBase (30 seconds).  Retrying a
        # commit or rollback after a transport/server error can turn a useful
        # error into a misleading "transaction does not exist" response, so
        # callers may explicitly make those requests single-attempt.
        attempts = max(1, max_attempts if max_attempts is not None else self.max_retries)
        for attempt in range(attempts):
            try:
                with request.urlopen(request.Request(url, data=body, headers=headers, method=method), timeout=self.timeout) as response:
                    raw = response.read()
                    return _decode_ejson(json.loads(raw.decode("utf-8"))) if raw else {}
            except error.HTTPError as exc:
                safe_body = exc.read(2048).decode("utf-8", "replace")
                if exc.code not in retryable or attempt + 1 >= attempts:
                    raise CloudbaseError(f"CloudBase HTTP {exc.code}: {safe_body}") from exc
            except (error.URLError, TimeoutError) as exc:
                if attempt + 1 >= attempts:
                    raise CloudbaseError(f"CloudBase network error: {type(exc).__name__}") from exc
            time.sleep(min(8, 2 ** attempt) + random.random() * 0.25)
        raise CloudbaseError("CloudBase request failed")

    def ensure_collection(self, name: str) -> None:
        try:
            self._request("POST", "/collections", {"collectionName": name})
        except CloudbaseError as exc:
            if "ALREADY_EXIST" not in str(exc) and "409" not in str(exc):
                raise

    def run_commands(self, commands: list[dict]) -> Any:
        return self._request("POST", "/commands", {"commands": commands})

    def get_document(self, collection: str, doc_id: str) -> dict | None:
        try:
            return self._request("GET", f"/collections/{parse.quote(collection)}/documents/{parse.quote(doc_id)}")
        except CloudbaseError as exc:
            if "DOCUMENT_NOT_FOUND" in str(exc) or "404" in str(exc):
                return None
            raise

    def upsert_many(self, collection: str, documents: Iterable[dict], *, batch_size: int = 80) -> int:
        docs = list(documents)
        written = 0
        for start in range(0, len(docs), batch_size):
            chunk = docs[start:start + batch_size]
            updates = []
            for doc in chunk:
                if not doc.get("_id"):
                    raise ValueError(f"{collection} document missing _id")
                updates.append({"q": {"_id": doc["_id"]}, "u": {"$set": doc}, "upsert": True, "multi": False})
            self._request("POST", "/commands", {"commands": [{"update": collection, "updates": updates}]})
            written += len(chunk)
        return written

    def count(self, collection: str, query: dict) -> int:
        # CloudBase REST's filtered-count endpoint is POST documents:count;
        # the documents listing endpoint may report a collection total but does
        # not provide a reliable filtered count.
        result = self._request("POST", f"/collections/{parse.quote(collection)}/documents:count", {
            "query": json.dumps(query, ensure_ascii=False, separators=(",", ":"))
        })
        data = result.get("data", result)
        return int(data.get("total", 0))

    def start_transaction(self) -> str:
        return self._request("POST", "/transactions")["transactionId"]

    def transaction_upsert(self, transaction_id: str, collection: str, document: dict) -> None:
        self._request("PATCH", f"/collections/{parse.quote(collection)}/documents/{parse.quote(document['_id'])}", {
            "data": {"$set": document}, "upsert": True, "returnDoc": False
        }, query={"transactionId": transaction_id}, max_attempts=1)

    def commit(self, transaction_id: str) -> None:
        self._request("POST", f"/transactions/{parse.quote(transaction_id)}/commit", max_attempts=1)

    def rollback(self, transaction_id: str) -> None:
        self._request("POST", f"/transactions/{parse.quote(transaction_id)}/rollback", max_attempts=1)


class MemoryCloudbaseClient:
    """Deterministic test double used by unit tests; never used silently in real mode."""

    def __init__(self, fail_collection: str | None = None):
        self.data: dict[str, dict[str, dict]] = defaultdict(dict)  # type: ignore[name-defined]
        self.fail_collection = fail_collection

    def ensure_collection(self, name: str) -> None:
        self.data.setdefault(name, {})

    def get_document(self, collection: str, doc_id: str) -> dict | None:
        value = self.data.get(collection, {}).get(doc_id)
        return dict(value) if value else None

    def upsert_many(self, collection: str, documents: Iterable[dict], *, batch_size: int = 80) -> int:
        if collection == self.fail_collection:
            raise CloudbaseError("injected failure")
        count = 0
        for doc in documents:
            self.data.setdefault(collection, {})[doc["_id"]] = dict(doc)
            count += 1
        return count

    def count(self, collection: str, query: dict) -> int:
        return sum(all(doc.get(key) == value for key, value in query.items()) for doc in self.data.get(collection, {}).values())

    def start_transaction(self) -> str:
        return "memory-tx"

    def transaction_upsert(self, transaction_id: str, collection: str, document: dict) -> None:
        self.data.setdefault(collection, {})[document["_id"]] = dict(document)

    def commit(self, transaction_id: str) -> None:
        return None

    def rollback(self, transaction_id: str) -> None:
        return None


from collections import defaultdict  # noqa: E402  (keeps public imports above concise)

