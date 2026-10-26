from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from urllib.parse import urlencode

from .config import SyncConfig


SCENES = ("daily-increase", "daily-decrease", "streak", "anomaly", "sector", "detail", "top10")


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _json(path: Path, value: object) -> None:
    _write(path, json.dumps(value, ensure_ascii=False, indent=2))


def _sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def build_payload(db_path: Path, requested_date: str | None = None) -> tuple[dict, dict]:
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        status = conn.execute("SELECT * FROM etf_calc_publish_status WHERE id='current'").fetchone()
        if not status:
            raise RuntimeError("本地龙虎榜尚未发布")
        date = requested_date or status["statistic_date"]
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("日期格式须为 YYYY-MM-DD") from exc
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM etf_derived_daily WHERE date=?", (date,)
        )]
        if not rows:
            raise RuntimeError(f"所选日期没有已发布派生结果：{date}")
        increase = sorted((row for row in rows if row["direction"] == "increase"),
                          key=lambda row: (-row["delta_shares"], row["code"]))[:10]
        decrease = sorted((row for row in rows if row["direction"] == "decrease"),
                          key=lambda row: (row["delta_shares"], row["code"]))[:10]
        streak = sorted((row for row in rows if row["streak_direction"] == "increase"),
                        key=lambda row: (-row["streak_days"], row["code"]))[:10]
        anomaly = sorted((row for row in rows if row["anomaly_qualified"]),
                         key=lambda row: (-row["anomaly_ratio"], row["code"]))[:10]
        sectors = [dict(row) for row in conn.execute(
            "SELECT * FROM etf_sector_daily WHERE date=? ORDER BY rank,sector", (date,)
        )]
        focus = (anomaly or increase or rows)[0]
        content = {
            "schema_version": "2.0", "batch_id": status["batch_id"], "trade_date": date,
            "baseline_date": increase[0]["baseline_date"] if increase else None,
            "quality_status": status["quality_status"],
            "coverage": json.loads(status["coverage_json"]),
            "rankings": {"increase": increase, "decrease": decrease, "streak": streak,
                         "anomaly": anomaly, "sectors": sectors},
            "focus": {"code": focus["code"], "name": focus["name"]},
            "disclaimer": "份额变化不代表未来涨跌，不等同资金净流入。",
        }
        audit = {
            "publishable": status["quality_status"] == "COMPLETE_FOR_COVERED_SAMPLE",
            "trade_date": date, "row_count": len(rows), "sector_count": len(sectors),
            "increase_count": sum(row["direction"] == "increase" for row in rows),
            "decrease_count": sum(row["direction"] == "decrease" for row in rows),
            "anomaly_count": sum(bool(row["anomaly_qualified"]) for row in rows),
            "source": "etf.db published calculation batch",
        }
        return content, audit
    finally:
        conn.close()


def export_pages(output: Path, base_url: str, content: dict) -> dict[str, str]:
    result = {}
    for scene in SCENES:
        query = {"capture": "1", "scene": scene, "date": content["trade_date"]}
        if scene == "detail":
            query["code"] = content["focus"]["code"]
        url = f"{base_url.rstrip('/')}/ranking-app?{urlencode(query)}"
        page = output / f"{scene}.html"
        _write(page, f"""<!doctype html><html><head><meta charset=\"utf-8\"><title>{scene}</title>
<style>html,body,iframe{{margin:0;width:100%;height:100%;border:0;background:#06182b}}</style></head>
<body><iframe src=\"{url}\" title=\"ETF ranking {scene}\"></iframe></body></html>""")
        result[scene] = str(page)
    return result


def _agent_browser(args: list[str], session: str) -> None:
    wrapper = Path(os.environ.get("APPDATA", "")) / "npm" / "agent-browser.ps1"
    shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if not wrapper.exists() or not shell:
        raise RuntimeError("agent-browser CLI 不可用，无法自动截图")
    command = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper),
               "--session", session, *args]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=45)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:] or result.stdout[-2000:])


def render_stills(output: Path, base_url: str, content: dict) -> dict[str, str]:
    session = f"etf-video-{content['trade_date'].replace('-', '')}-{os.getpid()}"
    result = {}
    try:
        for scene in SCENES:
            query = {"capture": "1", "scene": scene, "date": content["trade_date"]}
            if scene == "detail":
                query["code"] = content["focus"]["code"]
            url = f"{base_url.rstrip('/')}/ranking-app?{urlencode(query)}"
            target = output / f"{scene}.png"
            _agent_browser(["open", url], session)
            _agent_browser(["set", "viewport", "1080", "1920"], session)
            _agent_browser(["wait", "--fn", "window.__renderReady === true"], session)
            _agent_browser(["screenshot", str(target)], session)
            result[scene] = str(target)
    finally:
        try:
            _agent_browser(["close"], session)
        except Exception:
            pass
    return result


def write_narration(output: Path, content: dict) -> dict[str, str]:
    inc = content["rankings"]["increase"]
    dec = content["rankings"]["decrease"]
    top_inc = inc[0] if inc else None
    top_dec = dec[0] if dec else None
    lines = [
        f"{content['trade_date']}，ETF份额龙虎榜。",
        f"份额增加榜首是{top_inc['name']}。" if top_inc else "当日无可用份额增加榜。",
        f"份额减少榜首是{top_dec['name']}。" if top_dec else "当日无可用份额减少榜。",
        "连续变化和异动倍数仅用于数据观察。",
        content["disclaimer"],
    ]
    txt = output / "narration.txt"
    srt = output / "narration.srt"
    _write(txt, "\n".join(lines) + "\n")
    blocks = []
    for index, line in enumerate(lines):
        start, end = index * 5, (index + 1) * 5
        blocks.append(f"{index + 1}\n00:00:{start:02d},000 --> 00:00:{end:02d},000\n{line}\n")
    _write(srt, "\n".join(blocks))
    return {"text": str(txt), "srt": str(srt)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export ETF ranking video materials")
    parser.add_argument("--date", help="published trade date YYYY-MM-DD; defaults to current")
    parser.add_argument("--db", help="SQLite path")
    parser.add_argument("--base-url", default="http://127.0.0.1:5010")
    parser.add_argument("--output", help="output directory")
    parser.add_argument("--render", choices=("data", "stills", "all"), default="data")
    args = parser.parse_args(argv)
    config = SyncConfig.from_env(args.db)
    content, audit = build_payload(config.db_path, args.date)
    output = Path(args.output) if args.output else Path("output") / "etf-ranking-video" / content["trade_date"].replace("-", "")
    output.mkdir(parents=True, exist_ok=True)
    if not audit["publishable"]:
        _json(output / "_SKIP.json", audit)
        print(json.dumps({"status": "skipped", "audit": audit}, ensure_ascii=False, indent=2))
        return 2
    _json(output / "content.json", content)
    _json(output / "audit.json", audit)
    pages = export_pages(output, args.base_url, content)
    narration = write_narration(output, content)
    stills = render_stills(output, args.base_url, content) if args.render in {"stills", "all"} else {}
    files = [path for path in output.iterdir() if path.is_file()]
    manifest = {"schema_version": "2.0", "batch_id": content["batch_id"],
                "trade_date": content["trade_date"], "base_url": args.base_url,
                "pages": pages, "stills": stills, "narration": narration,
                "sha256": {path.name: _sha(path) for path in files}}
    _json(output / "manifest.json", manifest)
    print(json.dumps({"status": "ready", "output": str(output.resolve()),
                      "manifest": str((output / 'manifest.json').resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
