from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from .errors import SourceUnavailableError
from .utils import atomic_json, iso_now


class TradingCalendar:
    def __init__(self, cache_path: Path):
        self.cache_path = cache_path

    def _read_cache(self) -> tuple[set[str], str | None, str | None]:
        if not self.cache_path.exists():
            return set(), None, None
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            dates = set(payload.get("trading_dates", []))
            return dates, payload.get("range_start"), payload.get("range_end")
        except (OSError, ValueError, TypeError):
            return set(), None, None

    def _refresh(self) -> tuple[set[str], str, str]:
        try:
            import akshare as ak

            frame = ak.tool_trade_date_hist_sina()
            values = sorted({value.strftime("%Y-%m-%d") for value in frame["trade_date"]})
            if not values:
                raise ValueError("交易日历为空")
            atomic_json(
                self.cache_path,
                {
                    "updated_at": iso_now(),
                    "range_start": values[0],
                    "range_end": values[-1],
                    "trading_dates": values,
                },
            )
            return set(values), values[0], values[-1]
        except Exception as exc:
            raise SourceUnavailableError(f"无法刷新交易日历：{exc}") from exc

    def is_trading_day(self, target: date) -> bool:
        key = target.isoformat()
        dates, start, end = self._read_cache()
        if start and end and start <= key <= end:
            return key in dates
        try:
            dates, start, end = self._refresh()
        except SourceUnavailableError:
            if start and end and start <= key <= end:
                return key in dates
            raise
        if not (start <= key <= end):
            raise SourceUnavailableError(f"交易日历未覆盖目标日期 {key}（覆盖至 {end}）")
        return key in dates

