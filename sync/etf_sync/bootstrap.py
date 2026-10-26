from __future__ import annotations

import json
import sys
import argparse

from .config import SyncConfig
from .runner import COLLECTIONS, make_real_client


INDEXES = {
    "etf_master": [
        {"name": "search_keys_1", "key": {"search_keys": 1}},
    ],
    "etf_history": [
        {"name": "code_1_date_-1_release_seq_-1", "key": {"code": 1, "date": -1, "release_seq": -1}},
    ],
    "ranking_snapshots": [
        {"name": "release_direction_delta_code", "key": {"release_id": 1, "direction": 1, "delta_shares": -1, "code": 1}},
        {"name": "release_direction_delta_asc_code", "key": {"release_id": 1, "direction": 1, "delta_shares": 1, "code": 1}},
        {"name": "release_direction_rate_code", "key": {"release_id": 1, "direction": 1, "change_rate": -1, "code": 1}},
        {"name": "release_direction_rate_asc_code", "key": {"release_id": 1, "direction": 1, "change_rate": 1, "code": 1}},
        {"name": "release_sector_rate_code", "key": {"release_id": 1, "sector": 1, "change_rate": -1, "code": 1}},
        {"name": "release_code", "key": {"release_id": 1, "code": 1}, "unique": True},
    ],
    "streak_snapshots": [
        {"name": "release_direction_status_days_code", "key": {"release_id": 1, "direction": 1, "status": 1, "days": -1, "code": 1}},
        {"name": "release_code", "key": {"release_id": 1, "code": 1}, "unique": True},
    ],
    "anomaly_snapshots": [
        {"name": "release_qualified_ratio_code", "key": {"release_id": 1, "qualified": 1, "anomaly_ratio": -1, "code": 1}},
        {"name": "release_code", "key": {"release_id": 1, "code": 1}, "unique": True},
    ],
    "sector_snapshots": [
        {"name": "release_median_sector", "key": {"release_id": 1, "median_change_rate": -1, "sector": 1}},
    ],
    "user_watchlists": [
        {"name": "openid_created", "key": {"openid": 1, "created_at": -1}},
        {"name": "openid_code_unique", "key": {"openid": 1, "code": 1}, "unique": True},
    ],
    "user_members": [
        {"name": "openid_unique", "key": {"openid": 1}, "unique": True},
        {"name": "status_applied", "key": {"status": 1, "applied_at": 1}},
    ],
}


def _number(value):
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        for key in ("$numberInt", "$numberLong", "$numberDouble"):
            if key in value:
                try:
                    return float(value[key])
                except (TypeError, ValueError):
                    return None
    return None


def _command_state(response):
    """Extract success from the NoSQL REST /commands response without logging credentials."""
    results = response.get("list", []) if isinstance(response, dict) else []
    flattened = [item for group in results for item in (group if isinstance(group, list) else [group])]
    failures = []
    for item in flattened:
        if not isinstance(item, dict):
            continue
        ok = _number(item.get("ok"))
        if ok == 0 or item.get("code"):
            failures.append({key: item.get(key) for key in ("code", "errmsg", "message") if item.get(key)})
    return not failures, failures


def _index_names(value):
    names = []
    if isinstance(value, dict):
        if isinstance(value.get("name"), str):
            names.append(value["name"])
        for child in value.values():
            names.extend(_index_names(child))
    elif isinstance(value, list):
        for child in value:
            names.extend(_index_names(child))
    return sorted(set(names))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or verify ETF CloudBase collections and indexes")
    parser.add_argument("--verify", action="store_true", help="read index metadata only; do not create or modify resources")
    args = parser.parse_args(argv)
    config = SyncConfig.from_env()
    client = make_real_client(config)
    if args.verify:
        result = {}
        for collection in INDEXES:
            response = client.run_commands([{"listIndexes": collection}])
            ok, failures = _command_state(response)
            result[collection] = {"ok": ok, "index_names": _index_names(response), "failures": failures}
        failed = {name: item for name, item in result.items() if not item["ok"]}
        print(json.dumps({"verify": True, "indexes": result, "ok": not failed}, ensure_ascii=False, indent=2))
        return 1 if failed else 0
    for name in COLLECTIONS:
        client.ensure_collection(name)
    results = {}
    for collection, indexes in INDEXES.items():
        response = client.run_commands([{"createIndexes": collection, "indexes": indexes}])
        ok, failures = _command_state(response)
        results[collection] = {"ok": ok, "failures": failures}
    failed = {name: result for name, result in results.items() if not result["ok"]}
    print(json.dumps({"collections": list(COLLECTIONS), "indexes": results, "ok": not failed}, ensure_ascii=False, indent=2))
    if failed:
        raise RuntimeError("one or more index commands failed; inspect the reported collection failures")
    return 0


if __name__ == "__main__":
    sys.exit(main())

