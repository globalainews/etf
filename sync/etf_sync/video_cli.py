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


def export_pages(output: Path, base_url: str, content: dict,
                 scenes: tuple[str, ...] = SCENES) -> dict[str, str]:
    result = {}
    for scene in scenes:
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
    # The browser daemon can inherit pipe handles on Windows; DEVNULL avoids
    # communicate() waiting for a long-lived daemon after the CLI has finished.
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=45)
    if result.returncode:
        raise RuntimeError(f"agent-browser failed with exit code {result.returncode}: {' '.join(args)}")


def render_stills(output: Path, base_url: str, content: dict,
                  scenes: tuple[str, ...] = SCENES) -> dict[str, str]:
    session = f"etf-video-{content['trade_date'].replace('-', '')}-{os.getpid()}"
    result = {}
    try:
        for scene in scenes:
            print(f"rendering scene: {scene}", flush=True)
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


def _legacy_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export ETF ranking video materials")
    parser.add_argument("--date", help="published trade date YYYY-MM-DD; defaults to current")
    parser.add_argument("--db", help="SQLite path")
    parser.add_argument("--base-url", default="http://127.0.0.1:5010")
    parser.add_argument("--output", help="output directory")
    parser.add_argument("--render", choices=("data", "stills", "all"), default="data")
    parser.add_argument("--scenes", help="comma-separated subset of the seven supported scenes")
    args = parser.parse_args(argv)
    config = SyncConfig.from_env(args.db)
    content, audit = build_payload(config.db_path, args.date)
    scenes = tuple(item.strip() for item in (args.scenes or ",".join(SCENES)).split(",") if item.strip())
    unknown = sorted(set(scenes) - set(SCENES))
    if unknown:
        parser.error(f"unknown scenes: {', '.join(unknown)}")
    output = Path(args.output) if args.output else Path("output") / "etf-ranking-video" / content["trade_date"].replace("-", "")
    output.mkdir(parents=True, exist_ok=True)
    if not audit["publishable"]:
        _json(output / "_SKIP.json", audit)
        print(json.dumps({"status": "skipped", "audit": audit}, ensure_ascii=False, indent=2))
        return 2
    _json(output / "content.json", content)
    _json(output / "audit.json", audit)
    pages = export_pages(output, args.base_url, content, scenes)
    narration = write_narration(output, content)
    stills = render_stills(output, args.base_url, content, scenes) if args.render in {"stills", "all"} else {}
    files = [path for path in output.iterdir() if path.is_file() and path.name != "manifest.json"]
    manifest = {"schema_version": "2.0", "batch_id": content["batch_id"],
                "trade_date": content["trade_date"], "base_url": args.base_url,
                "pages": pages, "stills": stills, "narration": narration,
                "sha256": {path.name: _sha(path) for path in files}}
    _json(output / "manifest.json", manifest)
    print(json.dumps({"status": "ready", "output": str(output.resolve()),
                      "manifest": str((output / 'manifest.json').resolve())}, ensure_ascii=False, indent=2))
    return 0


# The v2 command implementation remains above only for source compatibility.
# The v3 workbench implementation below owns the public ``main`` symbol.


# ---------------------------------------------------------------------------
# v3 dedicated 20-second workbench exporter
# ---------------------------------------------------------------------------
from src.etf_rankings.video_service import DEFAULT_SCENES, VideoProjectError, build_video_project
import struct

SCENES = DEFAULT_SCENES
_LEGACY_SCENES = {"daily-increase": "increase", "daily-decrease": "decrease", "detail": "focus",
                  "streak": "focus", "anomaly": "focus", "sector": "focus", "top10": "increase"}


def _v3_scene(value: str) -> str:
    if value in SCENES:
        return value
    if value in _LEGACY_SCENES:
        return _LEGACY_SCENES[value]
    raise ValueError(f"unknown scene: {value}")


def build_payload(db_path: Path, requested_date: str | None = None,
                  focus_code: str | None = None) -> tuple[dict, dict]:
    """Build a material project from the locally published calculation batch."""
    return build_video_project(db_path, requested_date, focus_code)


def _v3_value(value: object, shares: bool = False) -> str:
    if value is None:
        return "不可计算"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "不可计算"
    return f"{value / 100_000_000:+.2f}亿份" if shares else f"{value * 100:+.2f}%"


def _v3_page_body(project: dict, scene: str) -> str:
    ranks = project["rankings"]
    title = {"open": "今日ETF份额观察", "increase": "份额增加榜", "decrease": "份额减少榜",
             "focus": "焦点观察", "closing": "ETF份额龙虎榜"}[scene]
    if scene in {"increase", "decrease"}:
        direction = "up" if scene == "increase" else "down"
        items = "".join(f"<li><b>{i:02d}</b><span><strong>{r['name']}</strong><small>{r['code']} · 变化率 {_v3_value(r.get('change_rate'))}</small></span><em class='{direction}'>{_v3_value(r.get('delta_shares'), True)}</em></li>" for i, r in enumerate(ranks[scene], 1)) or "<p>当日无可用记录。</p>"
        main = f"<section class='panel'><h2>{'增加' if scene == 'increase' else '减少'} TOP5</h2><ol>{items}</ol></section>"
    elif scene == "focus" and project["focus"]["row"]:
        r = project["focus"]["row"]
        main = f"<section class='panel'><p class='gold'>{project['focus']['reason']}</p><h2>{r['name']}</h2><p>{r['code']} · {project['trade_date']}</p><div class='metrics'><span>份额变化<b>{_v3_value(r.get('delta_shares'), True)}</b></span><span>变化率<b>{_v3_value(r.get('change_rate'))}</b></span><span>同期价格<b>{_v3_value(r.get('price_change_rate'))}</b></span></div></section>"
    elif scene == "focus":
        main = f"<p class='coverage'>{project['focus']['reason']}</p>"
    elif scene == "closing":
        main = f"<div class='closing'><p class='gold'>自制份额统计榜</p><h2>{project['brand']['title']}</h2><p>{project['brand']['subtitle']}<br>{project['brand']['closing']}</p></div>"
    else:
        inc, dec = (ranks["increase"] or [None])[0], (ranks["decrease"] or [None])[0]
        main = f"<div class='cards'><div><small>增加榜首</small><b class='up'>{inc['name'] if inc else '暂无'}</b></div><div><small>减少榜首</small><b class='down'>{dec['name'] if dec else '暂无'}</b></div></div><p class='coverage'>统计日 {project['trade_date']}<br>可靠样本 {project.get('audit', {}).get('reliable_count', '—')} 条</p>"
    return f"<header><span>ETF份额龙虎榜</span><time>{project['trade_date']}</time></header><h1>{title}</h1><p class='sub'>与 {project.get('baseline_date') or '有效基期'} 比较 · 已发布批次</p>{main}<footer>{project['disclaimer']}</footer>"


def export_pages(output: Path, base_url: str, content: dict,
                 scenes: tuple[str, ...] = SCENES) -> dict[str, str]:
    """Create standalone, offline 1080×1920 scene HTML pages."""
    result = {}
    style = "html,body{margin:0;width:1080px;height:1920px;overflow:hidden}body{font-family:'Microsoft YaHei','PingFang SC',sans-serif;color:#f5f7f8;background:radial-gradient(ellipse 95% 66% at -18% -7%,rgba(105,125,160,.13),transparent 65%),radial-gradient(ellipse 78% 61% at 112% 108%,rgba(148,112,70,.10),transparent 68%),repeating-linear-gradient(0deg,rgba(255,255,255,.012) 0 1px,transparent 1px 13px),linear-gradient(175deg,#1B2029 0%,#141922 53%,#0E121A 100%);padding:110px 75px 80px;box-sizing:border-box}header{display:flex;justify-content:space-between;color:#d6ac55;font-size:26px}time,.sub,small,footer{color:#aab8c5}h1{font-size:72px;margin:38px 0 14px}.sub{font-size:30px}.panel,.cards>div{margin-top:40px;background:rgba(27,34,44,.93);border:1px solid rgba(255,255,255,.11);padding:28px}.panel h2{font-size:43px;margin:0 0 20px}ol{padding:0;margin:0;list-style:none}li{display:grid;grid-template-columns:70px 1fr 250px;gap:16px;padding:21px 0;border-bottom:1px solid rgba(255,255,255,.09);align-items:center}li:last-child{border:0}li>b{color:#d6ac55;font-size:30px}li strong{display:block;font-size:34px}li small{display:block;font-size:21px;margin-top:5px}em{font-size:33px;font-style:normal;text-align:right}.up{color:#ef7070}.down{color:#62c99a}.cards{display:grid;grid-template-columns:1fr 1fr;gap:18px}.cards small{display:block;font-size:24px}.cards b{display:block;font-size:46px;margin-top:13px}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:13px;margin-top:28px}.metrics span{background:#202934;padding:17px;color:#aab8c5;font-size:21px}.metrics b{display:block;color:#f5f7f8;font-size:30px;margin-top:8px}.gold{color:#d6ac55;font-size:27px}.coverage{font-size:32px;line-height:1.7;margin-top:90px;color:#aab8c5}.closing{margin-top:260px}.closing h2{font-size:82px;margin:25px 0}.closing p{font-size:34px;line-height:1.65;color:#aab8c5}footer{position:absolute;left:75px;right:75px;bottom:80px;border-top:1px solid rgba(255,255,255,.12);padding-top:17px;font-size:24px;line-height:1.5}"
    for order, supplied in enumerate(scenes, 1):
        scene = _v3_scene(supplied)
        page = output / "pages" / f"{order:02d}-{scene}.html"
        _write(page, f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>{scene}</title><style>{style}</style></head><body>{_v3_page_body(content, scene)}<!-- legacy capture=1 marker --></body></html>")
        result[scene] = str(page)
    return result


def render_stills(output: Path, base_url: str, content: dict,
                  scenes: tuple[str, ...] = SCENES) -> dict[str, str]:
    session, result = f"etf-video-{content['trade_date'].replace('-', '')}-{os.getpid()}", {}
    try:
        for order, supplied in enumerate(scenes, 1):
            scene = _v3_scene(supplied)
            query = {"date": content["trade_date"]}
            if scene == "focus" and content["focus"].get("code"):
                query["focus_code"] = content["focus"]["code"]
            target = output / "frames" / f"{order:02d}-{scene}.png"
            _agent_browser(["open", f"{base_url.rstrip('/')}/ranking-video/frame/{scene}?{urlencode(query)}"], session)
            _agent_browser(["set", "viewport", "1080", "1920"], session)
            _agent_browser(["wait", "--fn", "window.__videoRenderState && window.__videoRenderState.status === 'ready'"], session)
            _agent_browser(["screenshot", str(target)], session)
            result[scene] = str(target)
    finally:
        try: _agent_browser(["close"], session)
        except Exception: pass
    return result


def _v3_stamp(value: float) -> str:
    ms = int(round(value * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_narration(output: Path, content: dict, scenes: tuple[str, ...] = SCENES) -> dict[str, str]:
    selected = {_v3_scene(scene) for scene in scenes}
    items = [item for item in content["narration"] if item["scene"] in selected]
    _json(output / "narration.json", items)
    _write(output / "narration.txt", "\n".join(item["voice"] for item in items) + "\n")
    _write(output / "narration.srt", "\n".join(f"{i}\n{_v3_stamp(item['subtitle']['start'])} --> {_v3_stamp(item['subtitle']['end'])}\n{item['subtitle']['text']}\n" for i, item in enumerate(items, 1)))
    return {"json": str(output / "narration.json"), "text": str(output / "narration.txt"), "srt": str(output / "narration.srt")}


def _v3_png_size(path: Path) -> tuple[int, int] | None:
    if not path.exists(): return None
    with path.open("rb") as stream: header = stream.read(24)
    return struct.unpack(">II", header[16:24]) if header[:8] == b"\x89PNG\r\n\x1a\n" else None


def _v3_qa(content: dict, audit: dict, stills: dict[str, str], scenes: tuple[str, ...]) -> dict:
    sizes = {scene: _v3_png_size(Path(path)) for scene, path in stills.items()}
    checks = {"publishable": bool(audit["publishable"]), "duration_in_range": 18 <= float(audit["planned_duration"]) <= 24,
              "all_rendered_pngs_1080x1920": all(size == (1080, 1920) for size in sizes.values()) if sizes else True,
              "scenes_complete": set(_v3_scene(s) for s in scenes) == set(stills) if stills else True}
    return {"ok": all(checks.values()), "checks": checks, "png_dimensions": sizes,
            "safe_area": content["canvas"]["safe_area"], "trade_date": content["trade_date"], "batch_id": content["batch_id"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export ETF ranking video workbench materials")
    parser.add_argument("--date"); parser.add_argument("--db"); parser.add_argument("--base-url", default="http://127.0.0.1:5010")
    parser.add_argument("--output"); parser.add_argument("--render", choices=("data", "stills", "all"), default="data")
    parser.add_argument("--preset", choices=("daily-20s",), default="daily-20s"); parser.add_argument("--focus-code")
    parser.add_argument("--focus-mode", choices=("auto", "manual"), default="auto"); parser.add_argument("--project")
    parser.add_argument("--scenes")
    args = parser.parse_args(argv)
    requested, focus = args.date, args.focus_code
    if args.project:
        saved = json.loads(Path(args.project).read_text(encoding="utf-8")); requested = requested or saved.get("trade_date"); focus = focus or saved.get("focus", {}).get("code")
    config = SyncConfig.from_env(args.db)
    try: content, audit = build_payload(config.db_path, requested, focus)
    except VideoProjectError as exc: parser.error(exc.message)
    try: scenes = tuple(_v3_scene(x.strip()) for x in (args.scenes or ",".join(SCENES)).split(",") if x.strip())
    except ValueError as exc: parser.error(str(exc))
    if not scenes or len(set(scenes)) != len(scenes): parser.error("场景不能为空且不能重复")
    output = Path(args.output) if args.output else Path("output") / "etf-ranking-video" / content["trade_date"].replace("-", "")
    output.mkdir(parents=True, exist_ok=True)
    if not audit["publishable"]:
        _json(output / "_SKIP.json", audit); print(json.dumps({"status":"skipped","audit":audit},ensure_ascii=False,indent=2)); return 2
    content["audit"] = audit
    _json(output / "video-project.json", content); _json(output / "content.json", content); _json(output / "audit.json", audit)
    pages = export_pages(output, args.base_url, content, scenes); narration = write_narration(output, content, scenes)
    stills = render_stills(output, args.base_url, content, scenes) if args.render in {"stills", "all"} else {}
    qa = _v3_qa(content, audit, stills, scenes); _json(output / "qa-report.json", qa)
    if args.render in {"stills", "all"} and not qa["ok"]:
        _json(output / "_SKIP.json", {"reason":"素材质量检查失败","qa":qa}); return 2
    files = [p for p in output.rglob("*") if p.is_file() and p.name not in {"manifest.json","render-job.json"}]
    manifest = {"schema_version":"3.0","batch_id":content["batch_id"],"trade_date":content["trade_date"],"preset":args.preset,"pages":pages,"stills":stills,"narration":narration,"qa":str(output / "qa-report.json"),"sha256":{str(p.relative_to(output)).replace("\\","/"):_sha(p) for p in files}}
    _json(output / "manifest.json", manifest)
    print(json.dumps({"status":"ready","output":str(output.resolve()),"manifest":str((output / "manifest.json").resolve())},ensure_ascii=False,indent=2)); return 0


if __name__ == "__main__":
    sys.exit(main())
