from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from .errors import LockBusyError


SHANGHAI = ZoneInfo("Asia/Shanghai")
_ZH_DIGITS = "零一二三四五六七八九"


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI)


def iso_now() -> str:
    return now_shanghai().isoformat(timespec="seconds")


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(value, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quantized(value: float, digits: int = 2) -> Decimal:
    step = Decimal(1).scaleb(-digits)
    return Decimal(str(value)).quantize(step, rounding=ROUND_HALF_UP)


def trimmed_decimal(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def format_share_change(value: float) -> str:
    sign = "+" if value > 0 else "-"
    absolute = abs(value)
    if absolute >= 100_000_000:
        number, unit = absolute / 100_000_000, "亿份"
    elif absolute >= 10_000:
        number, unit = absolute / 10_000, "万份"
    else:
        number, unit = absolute, "份"
    return f"{sign}{trimmed_decimal(quantized(number))}{unit}"


def _integer_to_chinese(number: int) -> str:
    if number == 0:
        return "零"
    if number >= 100_000_000:
        high, low = divmod(number, 100_000_000)
        return _integer_to_chinese(high) + "亿" + (_integer_to_chinese(low) if low else "")
    if number >= 10_000:
        high, low = divmod(number, 10_000)
        bridge = "零" if low and low < 1000 else ""
        return _integer_to_chinese(high) + "万" + bridge + (_integer_to_chinese(low) if low else "")
    units = ((1000, "千"), (100, "百"), (10, "十"), (1, ""))
    result: list[str] = []
    pending_zero = False
    remaining = number
    for divisor, label in units:
        digit, remaining = divmod(remaining, divisor)
        if digit:
            if pending_zero and result:
                result.append("零")
            if not (divisor == 10 and digit == 1 and not result):
                result.append(_ZH_DIGITS[digit])
            result.append(label)
            pending_zero = False
        elif result and remaining:
            pending_zero = True
    return "".join(result)


def number_to_chinese(value: Decimal) -> str:
    text = trimmed_decimal(value)
    integer, dot, fraction = text.partition(".")
    result = _integer_to_chinese(int(integer))
    if dot and fraction:
        result += "点" + "".join(_ZH_DIGITS[int(ch)] for ch in fraction)
    return result


def spoken_share_change(value: float) -> str:
    absolute = abs(value)
    if absolute >= 100_000_000:
        number, unit = absolute / 100_000_000, "亿份"
    elif absolute >= 10_000:
        number, unit = absolute / 10_000, "万份"
    else:
        number, unit = absolute, "份"
    return number_to_chinese(quantized(number)) + unit


def spoken_name(name: str) -> str:
    return re.sub(r"\d+", lambda match: _integer_to_chinese(int(match.group())), name)


def chinese_date(value: date) -> str:
    return f"{_integer_to_chinese(value.month)}月{_integer_to_chinese(value.day)}日"


@contextmanager
def single_instance_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LockBusyError(f"已有 ETF 龙虎榜生产任务正在运行：{path}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} started={iso_now()}\n".encode("utf-8"))
        handle.flush()
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

