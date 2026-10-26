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

    @staticmethod
    def _command_groups(result: Any) -> list[list[Any]]:
        """Normalize the documented runCommands result, preserving each command."""
        value = result.get("list", result.get("data", {}).get("list", [])) if isinstance(result, dict) else []
        if not isinstance(value, list) or not value:
            return []
        return value if isinstance(value[0], list) else [value]

    @classmethod
    def _command_rows(cls, result: Any) -> list[Any]:
        """Return rows for the first command, used by single-command helpers."""
        groups = cls._command_groups(result)
        return groups[0] if groups else []

    def get_document(self, collection: str, doc_id: str) -> dict | None:
        try:
            return self._request("GET", f"/collections/{parse.quote(collection)}/documents/{parse.quote(doc_id)}")
        except CloudbaseError as exc:
            if "DOCUMENT_NOT_FOUND" in str(exc) or "404" in str(exc):
                return None
            raise

    def find_documents(self, collection: str, query: dict, *, limit: int = 1, skip: int = 0) -> list[dict]:
        """Read through runCommands, the same documented API as writes."""
        result = self.run_commands([{
            "find": collection, "filter": query, "limit": limit, "skip": skip,
        }])
        rows = self._command_rows(result)
        parsed: list[dict] = []
        for row in rows:
            value = _decode_ejson(json.loads(row)) if isinstance(row, str) else row
            if isinstance(value, dict):
                parsed.append(value)
        return parsed

    def first_document(self, collection: str) -> dict | None:
        rows = self.find_documents(collection, {}, limit=1)
        return rows[0] if rows else None

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
            result = self._request("POST", "/commands", {"commands": [{"update": collection, "updates": updates}]})
            rows = self._command_rows(result)
            if not rows:
                raise CloudbaseError(f"CloudBase update returned no result for {collection}")
            changed = 0
            for row in rows:
                if not isinstance(row, dict):
                    raise CloudbaseError(f"CloudBase update returned an invalid result for {collection}")
                if row.get("ok") in (0, False) or row.get("code"):
                    raise CloudbaseError(f"CloudBase update failed for {collection}: {row.get('message') or row}")
                changed += int(row.get("n", 0))
            if changed < len(chunk):
                raise CloudbaseError(
                    f"CloudBase update acknowledged {changed} of {len(chunk)} documents for {collection}"
                )
            written += len(chunk)
        return written

    def count(self, collection: str, query: dict) -> int:
        # Keep reads and writes on runCommands.  The gateway's list/count
        # convenience endpoints differ by API version, while runCommands is
        # explicitly documented for MongoDB-style count and find commands.
        rows = self._command_rows(self.run_commands([{"count": collection, "query": query}]))
        if not rows or not isinstance(rows[0], dict):
            raise CloudbaseError(f"CloudBase count returned no result for {collection}")
        value = rows[0].get("n", rows[0].get("total"))
        if value is None:
            raise CloudbaseError(f"CloudBase count returned no numeric total for {collection}")
        return int(value)

    def start_transaction(self) -> str:
        return self._request("POST", "/transactions")["transactionId"]

    def transaction_upsert(self, transaction_id: str, collection: str, document: dict) -> None:
        self._request("PATCH", f"/collections/{parse.quote(collection)}/documents/{parse.quote(document['_id'])}", {
            "data": {"$set": document}, "upsert": True, "returnDoc": False
        }, query={"transactionId": transaction_id}, max_attempts=1)

    def transaction_upsert_many(self, transaction_id: str,
                                documents: list[tuple[str, dict]]) -> None:
        """Upsert several documents in one transactional HTTP round trip.

        CloudBase transactions expire after 30 seconds.  Its documented
        runCommands API accepts multiple commands plus transactionId, avoiding
        one slow PATCH request per collection before the commit.
        """
        commands = []
        for collection, document in documents:
            if not document.get("_id"):
                raise ValueError(f"{collection} transaction document missing _id")
            commands.append({
                "update": collection,
                "updates": [{"q": {"_id": document["_id"]},
                             "u": {"$set": document}, "upsert": True, "multi": False}],
            })
        result = self._request(
            "POST", "/commands", {"commands": commands, "transactionId": transaction_id},
            max_attempts=1,
        )
        groups = self._command_groups(result)
        if len(groups) != len(commands):
            raise CloudbaseError("CloudBase transactional update returned incomplete results")
        for (collection, _), rows in zip(documents, groups):
            if not rows or not isinstance(rows[0], dict):
                raise CloudbaseError(f"CloudBase transactional update returned no result for {collection}")
            row = rows[0]
            if row.get("ok") in (0, False) or row.get("code") or int(row.get("n", 0)) < 1:
                raise CloudbaseError(
                    f"CloudBase transactional update failed for {collection}: {row.get('message') or row}"
                )

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

    def first_document(self, collection: str) -> dict | None:
        values = self.data.get(collection, {})
        return dict(next(iter(values.values()))) if values else None

    def find_documents(self, collection: str, query: dict, *, limit: int = 1, skip: int = 0) -> list[dict]:
        return [dict(document) for document in self.data.get(collection, {}).values()
                if all(document.get(key) == value for key, value in query.items())][skip:skip + limit]

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

    def transaction_upsert_many(self, transaction_id: str,
                                documents: list[tuple[str, dict]]) -> None:
        for collection, document in documents:
            self.transaction_upsert(transaction_id, collection, document)

    def commit(self, transaction_id: str) -> None:
        return None

    def rollback(self, transaction_id: str) -> None:
        return None


from collections import defaultdict  # noqa: E402  (keeps public imports above concise)

