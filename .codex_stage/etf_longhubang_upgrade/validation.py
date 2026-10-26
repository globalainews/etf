from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from .errors import PackageValidationError
from .utils import format_share_change, spoken_name


REMOTE_RE = re.compile(r"(?:https?:)?//", re.IGNORECASE)
ABSOLUTE_RE = re.compile(r"(?:[A-Za-z]:[\\/]|file:|(?:src|href)\s*=\s*[\"']\s*[\\/])", re.IGNORECASE)


def estimate_speech_seconds(text: str) -> float:
    spoken = re.sub(r"[，。；：？！,.!?;:\s]", "", text)
    pauses = len(re.findall(r"[，。；：？！,.!?;:]", text))
    return len(spoken) / 5.5 + pauses * 0.12


def validate_schema(payload: dict, schema_path: Path) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        details = "; ".join(f"{'.'.join(map(str, error.path)) or '<root>'}: {error.message}" for error in errors[:8])
        raise PackageValidationError(f"主 JSON Schema 校验失败：{details}")


def validate_business(payload: dict, pages: dict[str, str]) -> dict:
    issues: list[str] = []
    all_codes: set[str] = set()
    for side, expected_sign, direction in (("increase", 1, "增加"), ("decrease", -1, "减少")):
        items = payload["rankings"][side]
        if len(items) != 5:
            issues.append(f"{side} must contain 5 items")
            continue
        if [item["rank"] for item in items] != [1, 2, 3, 4, 5]:
            issues.append(f"{side} ranks are not consecutive")
        magnitudes = [abs(float(item["share_change"])) for item in items]
        if magnitudes != sorted(magnitudes, reverse=True):
            issues.append(f"{side} is not sorted by absolute share change")
        for item in items:
            change = float(item["share_change"])
            if change * expected_sign <= 0:
                issues.append(f"wrong sign: {item['fund_code']}")
            if item["fund_code"] in all_codes:
                issues.append(f"duplicate code: {item['fund_code']}")
            all_codes.add(item["fund_code"])
            if abs((float(item["shares_current"]) - float(item["shares_previous"])) - change) > 0.5:
                issues.append(f"share arithmetic mismatch: {item['fund_code']}")
            expected_pct = None if float(item["shares_previous"]) == 0 else change / float(item["shares_previous"]) * 100
            actual_pct = item.get("share_change_pct")
            if expected_pct is None and actual_pct is not None:
                issues.append(f"percentage should be null: {item['fund_code']}")
            # Upstream providers commonly persist the percentage rounded to four
            # decimal places.  Treat 0.0001 percentage point as representation
            # tolerance while keeping the exact share arithmetic authoritative.
            if expected_pct is not None and (actual_pct is None or abs(float(actual_pct) - expected_pct) > 1e-4):
                issues.append(f"percentage mismatch: {item['fund_code']}")
            if item["display_value"] != format_share_change(change):
                issues.append(f"display value mismatch: {item['fund_code']}")
        page_index = 1 if side == "increase" else 2
        page = payload["pages"][page_index]
        html = pages.get(page["html_file"], "")
        for item in items:
            if item["fund_name"] not in html or item["display_value"] not in html or item["fund_code"] not in html:
                issues.append(f"HTML content mismatch: {item['fund_code']}")
            if spoken_name(item["fund_name"]) not in page["tts_text"]:
                issues.append(f"TTS content mismatch: {item['fund_code']}")
    expected_pages = [
        (1, "cover", "page_01_cover.html"),
        (2, "increase_top5", "page_02_increase.html"),
        (3, "decrease_top5", "page_03_decrease.html"),
        (4, "close", "page_04_close.html"),
    ]
    actual_pages = [(page["sequence"], page["page_id"], page["html_file"]) for page in payload["pages"]]
    if actual_pages != expected_pages:
        issues.append("fixed page mapping mismatch")
    for page in payload["pages"]:
        if page["min_duration_sec"] > page["max_duration_sec"]:
            issues.append(f"invalid duration range: {page['page_id']}")
        if any(len(sentence) > 64 for sentence in page["subtitle_sentences"]):
            issues.append(f"subtitle exceeds schema limit: {page['page_id']}")
        html = pages.get(page["html_file"])
        if not html:
            issues.append(f"missing HTML: {page['html_file']}")
    close_page = payload["pages"][3]
    if close_page["headline"] == "今日数据结论":
        close_copy = close_page["tts_text"]
        for required in ("份额变化不等于资金净流入", "不代表价格涨跌", "不构成投资建议"):
            if required not in close_copy:
                issues.append(f"close narration missing required statement: {required}")
        for page in payload["pages"]:
            if any(sentence.endswith("。") for sentence in page["subtitle_sentences"]):
                issues.append(f"subtitle must not end with Chinese full stop: {page['page_id']}")
    for filename, html in pages.items():
        if payload["trade_date"] not in html:
            issues.append(f"trade date missing from HTML: {filename}")
        if "自制份额统计榜" not in html:
            issues.append(f"column identity missing from HTML: {filename}")
        if 'data-render-mode' not in html or '?mode=render' in html:
            issues.append(f"deterministic render mode missing: {filename}")
    total_estimate = sum(estimate_speech_seconds(page["tts_text"]) for page in payload["pages"])
    if issues:
        raise PackageValidationError("业务一致性校验失败：" + "; ".join(issues))
    return {"status": "ok", "estimated_narration_seconds": round(total_estimate, 2)}


def validate_html_security(pages: dict[str, str]) -> dict:
    issues: list[str] = []
    for name, html in pages.items():
        if REMOTE_RE.search(html):
            issues.append(f"{name}: remote or protocol-relative URL")
        if ABSOLUTE_RE.search(html):
            issues.append(f"{name}: absolute path or file URL")
        if ".." in html:
            issues.append(f"{name}: parent path token")
        if 'data-render-ready="true"' not in html and "dataset.renderReady" not in html:
            issues.append(f"{name}: render-ready marker missing")
    if issues:
        raise PackageValidationError("HTML 安全校验失败：" + "; ".join(issues))
    return {"status": "ok", "network_dependencies": 0}

