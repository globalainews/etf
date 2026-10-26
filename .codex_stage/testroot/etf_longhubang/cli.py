from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from .errors import ProducerError
from .pipeline import Settings, build
from .publish import write_skip
from .utils import now_shanghai, single_instance_lock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = Path(r"D:\claude projects\ETF")
DEFAULT_PYTHON = Path(r"F:\anaconda3\python.exe")


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期格式必须为 YYYY-MM-DD") from exc


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="生产每日 ETF 份额龙虎榜素材包")
    result.add_argument("--date", type=_parse_date, help="目标自然日 YYYY-MM-DD；默认上海时区昨天")
    result.add_argument("--publish-date", type=_parse_date, help="计划发布日期；默认上海时区今天")
    result.add_argument("--skip-refresh", action="store_true", help="跳过上游份额更新，仅使用现有数据库")
    result.add_argument("--db", type=Path, default=DEFAULT_SOURCE_ROOT / "etf.db")
    result.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "ETF")
    result.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    result.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    result.add_argument("--schema", type=Path, default=PROJECT_ROOT / "docs" / "schemas" / "etf_longhubang.v1.schema.json")
    result.add_argument("--force-revision", action="store_true", help="即使内容相同也生成下一版本")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    today = now_shanghai().date()
    target = args.date or today - timedelta(days=1)
    publish_date = args.publish_date or today
    output_root = args.output_root.resolve()
    archive_root = output_root.parent / f"{output_root.name}_archive"
    audit_root = PROJECT_ROOT / "output" / "etf_longhubang_runs"
    settings = Settings(
        target_date=target,
        publish_date=publish_date,
        db_path=args.db.resolve(),
        output_root=output_root,
        archive_root=archive_root,
        audit_root=audit_root,
        schema_path=args.schema.resolve(),
        calendar_cache=PROJECT_ROOT / "cache" / "etf_longhubang" / "trading_calendar.json",
        source_root=args.source_root.resolve(),
        python=args.python.resolve(),
        skip_refresh=args.skip_refresh,
        force_revision=args.force_revision,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        with single_instance_lock(audit_root / ".producer.lock"):
            result = build(settings)
            if result["status"] == "skipped":
                path = write_skip(output_root, archive_root, target.isoformat(), "non_trading_day", "非交易日")
                result["skip_dir"] = str(path) if path else None
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
    except ProducerError as exc:
        if exc.reason_code:
            try:
                write_skip(output_root, archive_root, target.isoformat(), exc.reason_code, str(exc))
            except Exception as skip_exc:
                print(f"[WARN] 无法写入跳过回执：{skip_exc}", file=sys.stderr)
        print(f"[FAIL] {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        print(f"[FAIL] 未处理异常：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

