from __future__ import annotations

import argparse
import json
import sys

from .config import SyncConfig
from .member_store import list_members, pull_members, push_members
from .runner import make_real_client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synchronize local ETF member review records with CloudBase")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pull", action="store_true", help="pull CloudBase applications into local SQLite")
    group.add_argument("--push", action="store_true", help="push local approved/manual dirty records to CloudBase")
    group.add_argument("--status", action="store_true", help="show local member review and sync counts")
    args = parser.parse_args(argv)
    config = SyncConfig.from_env()
    if args.status:
        result = list_members(db_path=str(config.db_path))
    else:
        client = make_real_client(config)
        result = pull_members(client, db_path=str(config.db_path)) if args.pull else push_members(client, db_path=str(config.db_path))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
