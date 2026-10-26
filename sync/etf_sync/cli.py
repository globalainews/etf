from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .config import SyncConfig
from .runner import export_bundle, make_real_client, prepare_bundle, summarize, sync_bundle


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Export and synchronize ETF mini-program data")
    result.add_argument("--db", help="SQLite path; defaults to ETF_DB_PATH or project etf.db")
    result.add_argument("--initial", action="store_true", help="include the latest 90 available share dates")
    result.add_argument("--correction-from", help="re-export revisions from YYYY-MM-DD")
    result.add_argument("--dry-run", action="store_true", help="validate and summarize without cloud writes")
    result.add_argument("--output", help="optional JSON bundle output path")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = SyncConfig.from_env(args.db)
    client = None if args.dry_run else make_real_client(config)
    bundle = prepare_bundle(config, client, initial=args.initial, correction_from=args.correction_from)
    if args.output:
        export_bundle(bundle, Path(args.output))
    result = summarize(bundle) if args.dry_run else sync_bundle(client, bundle, batch_size=config.batch_size)
    result["mode"] = "dry-run" if args.dry_run else "real"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

