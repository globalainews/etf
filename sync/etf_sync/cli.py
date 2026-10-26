from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .config import SyncConfig
from .runner import (diagnose_remote_bundle, export_bundle, make_real_client,
                     prepare_bundle, summarize, sync_bundle)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Export and synchronize ETF mini-program data")
    result.add_argument("--db", help="SQLite path; defaults to ETF_DB_PATH or project etf.db")
    result.add_argument("--initial", action="store_true", help="include the latest 90 available share dates")
    result.add_argument("--correction-from", help="re-export revisions from YYYY-MM-DD")
    result.add_argument("--dry-run", action="store_true", help="validate and summarize without cloud writes")
    result.add_argument(
        "--resume-publish",
        action="store_true",
        help="validate a previously uploaded failed batch and only retry the publish pointer",
    )
    result.add_argument(
        "--diagnose-cloud",
        action="store_true",
        help="read-only inventory of the configured CloudBase environment and local release",
    )
    result.add_argument("--output", help="optional JSON bundle output path")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.resume_publish and args.dry_run:
        parser().error("--resume-publish cannot be combined with --dry-run")
    if args.diagnose_cloud and (args.dry_run or args.resume_publish):
        parser().error("--diagnose-cloud cannot be combined with --dry-run or --resume-publish")
    config = SyncConfig.from_env(args.db)
    client = None if args.dry_run else make_real_client(config)
    if args.diagnose_cloud:
        bundle = prepare_bundle(config, None, initial=args.initial, correction_from=args.correction_from)
        print(json.dumps(diagnose_remote_bundle(client, bundle, env_id=config.env_id), ensure_ascii=False, indent=2))
        return 0
    bundle = prepare_bundle(
        config,
        client,
        initial=args.initial,
        correction_from=args.correction_from,
        resume_publish=args.resume_publish,
    )
    if args.output:
        export_bundle(bundle, Path(args.output))
    result = summarize(bundle) if args.dry_run else sync_bundle(
        client,
        bundle,
        batch_size=config.batch_size,
        resume_publish=args.resume_publish,
    )
    result["mode"] = "dry-run" if args.dry_run else "real"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

