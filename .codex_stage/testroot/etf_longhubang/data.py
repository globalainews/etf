from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import InsufficientDataError, SourceUnavailableError
from .utils import format_share_change


MIN_COMMON_COVERAGE = 0.95


@dataclass(frozen=True)
class Snapshot:
    trade_date: str
    previous_trade_date: str
    increase: list[dict]
    decrease: list[dict]
    audit: dict


def refresh_scale(python: Path, source_root: Path, target_date: str, log_path: Path) -> None:
    command = [str(python), "-X", "utf8", str(source_root / "daily_update.py"), "--date", target_date.replace("-", ""), "--scale-only"]
    result = subprocess.run(
        command,
        cwd=source_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"command={subprocess.list2cmdline(command)}\nexit_code={result.returncode}\n\nSTDOUT\n{result.stdout}\n\nSTDERR\n{result.stderr}",
        encoding="utf-8",
    )
    if result.returncode:
        tail = (result.stderr or result.stdout)[-800:].strip()
        raise SourceUnavailableError(f"份额更新失败（exit={result.returncode}）：{tail}")


def _market(code: str, market: str) -> tuple[str, str]:
    normalized = (market or "").strip().lower()
    if normalized in {"上海", "上交所", "sse", "sh"} or code.startswith(("5", "6")):
        return f"{code}.SH", "SSE"
    if normalized in {"深圳", "深交所", "szse", "sz"} or code.startswith(("1", "3")):
        return f"{code}.SZ", "SZSE"
    raise InsufficientDataError(f"无法识别 ETF {code} 的交易所：{market!r}")


def load_snapshot(db_path: Path, target_date: str) -> Snapshot:
    if not db_path.exists():
        raise SourceUnavailableError(f"ETF 数据库不存在：{db_path}")
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        target_count = conn.execute("SELECT COUNT(*) FROM fund_scale WHERE date=?", (target_date,)).fetchone()[0]
        if not target_count:
            raise SourceUnavailableError(f"交易日 {target_date} 更新后仍无份额数据")
        previous = conn.execute("SELECT MAX(date) FROM fund_scale WHERE date < ?", (target_date,)).fetchone()[0]
        if not previous:
            raise InsufficientDataError(f"{target_date} 没有可比较的前一有效交易日")
        previous_count = conn.execute("SELECT COUNT(*) FROM fund_scale WHERE date=?", (previous,)).fetchone()[0]
        market_rows = conn.execute(
            "SELECT f.market, COUNT(*) n FROM fund_scale s JOIN funds f USING(code) WHERE s.date=? GROUP BY f.market",
            (target_date,),
        ).fetchall()
        market_counts = {str(row["market"]): row["n"] for row in market_rows}
        recognized_markets = {"SSE": 0, "SZSE": 0}
        for market, count in market_counts.items():
            key = "SSE" if market.lower() in {"上海", "上交所", "sse", "sh"} else "SZSE" if market.lower() in {"深圳", "深交所", "szse", "sz"} else None
            if key:
                recognized_markets[key] += count
        if not all(recognized_markets.values()):
            raise InsufficientDataError(f"目标日交易所数据不完整：{recognized_markets}")
        rows = conn.execute(
            """
            SELECT cur.code, f.name, f.market, cur.shares current_shares, prev.shares previous_shares
            FROM fund_scale cur
            JOIN fund_scale prev ON prev.code=cur.code AND prev.date=?
            JOIN funds f ON f.code=cur.code
            WHERE cur.date=?
            """,
            (previous, target_date),
        ).fetchall()
        common_count = len(rows)
        coverage = min(common_count / target_count, common_count / previous_count)
        if coverage < MIN_COMMON_COVERAGE:
            raise InsufficientDataError(
                f"相邻交易日共同样本覆盖率仅 {coverage:.1%}，低于 {MIN_COMMON_COVERAGE:.0%}"
            )
        increase: list[dict] = []
        decrease: list[dict] = []
        for row in rows:
            current = float(row["current_shares"])
            prior = float(row["previous_shares"])
            change = current - prior
            if change == 0:
                continue
            code, exchange = _market(str(row["code"]).zfill(6), str(row["market"]))
            item = {
                "fund_code": code,
                "fund_name": str(row["name"]).strip(),
                "exchange": exchange,
                "share_change": change,
                "shares_current": current,
                "shares_previous": prior,
                "share_change_pct": change / prior * 100 if prior else None,
                "display_value": format_share_change(change),
            }
            (increase if change > 0 else decrease).append(item)
        increase.sort(key=lambda item: (-abs(item["share_change"]), item["fund_code"]))
        decrease.sort(key=lambda item: (-abs(item["share_change"]), item["fund_code"]))
        if len(increase) < 5 or len(decrease) < 5:
            raise InsufficientDataError(f"正负变化样本不足：增加 {len(increase)}，减少 {len(decrease)}")
        for collection in (increase, decrease):
            for rank, item in enumerate(collection[:5], 1):
                item["rank"] = rank
        return Snapshot(
            trade_date=target_date,
            previous_trade_date=previous,
            increase=increase[:5],
            decrease=decrease[:5],
            audit={
                "target_count": target_count,
                "previous_count": previous_count,
                "common_count": common_count,
                "common_coverage": round(coverage, 6),
                "market_counts": recognized_markets,
            },
        )
    finally:
        conn.close()
