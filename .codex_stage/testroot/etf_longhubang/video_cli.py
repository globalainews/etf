from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .video import ROOT, STAGES, VideoBuildError, VideoContext, build, probe


def resolve_date(value: str | None, input_root: Path) -> str:
    if value:
        compact = value.replace("-", "")
        try:
            return datetime.strptime(compact, "%Y%m%d").strftime("%Y%m%d")
        except ValueError as exc:
            raise argparse.ArgumentTypeError("日期必须为 YYYYMMDD 或 YYYY-MM-DD") from exc
    ready_dates = sorted(
        path.name
        for path in input_root.glob("????????")
        if path.is_dir() and path.name.isdigit() and (path / "_READY.json").exists()
    )
    if not ready_dates:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
        raise argparse.ArgumentTypeError(f"{input_root} 下没有可用的 _READY 素材包（当前日期 {today}）")
    return ready_dates[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ETF龙虎榜短视频生产")
    parser.add_argument("--date", help="素材交易日，YYYYMMDD 或 YYYY-MM-DD；默认上海时区今天")
    parser.add_argument("--input-root", type=Path, default=ROOT / "ETF")
    parser.add_argument("--work-root", type=Path, default=ROOT / "cache" / "etf_longhubang_video")
    parser.add_argument("--output-root", type=Path, default=ROOT / "output" / "ETF")
    parser.add_argument("--schema", type=Path, default=ROOT / "docs" / "schemas" / "etf_longhubang.v1.schema.json")
    parser.add_argument("--from-stage", choices=STAGES)
    parser.add_argument("--to-stage", choices=STAGES)
    parser.add_argument("--force", action="store_true", help="忽略阶段缓存并重新生成")
    parser.add_argument("--skip-cache", action="store_true", help="本次运行不读取阶段缓存")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    try:
        date = resolve_date(args.date, args.input_root.resolve())
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    ctx = VideoContext(
        date=date,
        input_root=args.input_root.resolve(),
        work_root=args.work_root.resolve(),
        output_root=args.output_root.resolve(),
        schema_path=args.schema.resolve(),
        force=args.force,
        skip_cache=args.skip_cache,
        verbose=args.verbose,
    )
    try:
        result = build(ctx, args.from_stage, args.to_stage, args.dry_run)
        final = ctx.output / f"ETF龙虎榜_{date}.mp4"
        if final.exists() and "finalize" in result["outputs"]:
            info = probe(final)
            duration = float(info.get("format", {}).get("duration", 0) or 0)
            print("=" * 56)
            print("ETF龙虎榜制作完成")
            print(f"交易日：{date}")
            print(f"视频：{final}")
            print(f"时长：{duration:.1f} 秒")
            print(f"文件大小：{final.stat().st_size / 1024 / 1024:.1f} MB")
            print(f"耗时：{result['seconds']:.1f} 秒")
            print("=" * 56)
        else:
            completed = "、".join(result["outputs"]) or "无"
            print(f"阶段运行完成：{completed}")
        return 0
    except VideoBuildError as exc:
        print(f"[FAIL] stage={exc.stage}: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
