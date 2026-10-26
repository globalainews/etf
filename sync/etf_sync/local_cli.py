from __future__ import annotations

import argparse
import json
import sys

from .config import SyncConfig
from .local_store import materialize, verify


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Materialize ETF ranking calculations in local SQLite")
    modes = result.add_mutually_exclusive_group(required=True)
    modes.add_argument("--full", action="store_true", help="rebuild all available history")
    modes.add_argument("--incremental", action="store_true", help="publish dates newer than current local release")
    modes.add_argument("--correction-from", help="recompute and replace results from YYYY-MM-DD")
    modes.add_argument("--verify", action="store_true", help="verify the current published local release")
    result.add_argument("--dry-run", action="store_true", help="show planned row counts without writes")
    result.add_argument("--db", help="SQLite path; defaults to ETF_DB_PATH or project etf.db")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = SyncConfig.from_env(args.db)
    if args.verify:
        result = verify(config.db_path)
    else:
        result = materialize(config, full=args.full, incremental=args.incremental,
                             correction_from=args.correction_from, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
