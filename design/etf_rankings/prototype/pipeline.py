from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from .data_engine import DB_PATH, MODULE_ROOT, build_content, file_sha256, load_json
from .video import render_video


DELIVERY_ROOT = MODULE_ROOT.parent
RUNS_ROOT = MODULE_ROOT / "runs"
WEB_ROOT = MODULE_ROOT / "web"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_agent_browser(args: list[str], session: str) -> str:
    wrapper = Path(os.environ.get("APPDATA", "")) / "npm" / "agent-browser.ps1"
    shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if not wrapper.exists() or not shell:
        raise FileNotFoundError("agent-browser CLI not found")
    command = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper), "--session", session, *args]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    if result.returncode:
        raise RuntimeError(f"agent-browser failed with exit code {result.returncode}: {' '.join(args)}")
    return "ok"


def render_stills(content: dict) -> dict[str, str]:
    latest = RUNS_ROOT / "latest"
    write_json(latest / "content.json", content)
    handler = partial(SimpleHTTPRequestHandler, directory=str(MODULE_ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    session = f"etf-render-{content['meta']['run_id']}"
    outputs = {
        "daily": DELIVERY_ROOT / "daily_ranking.png",
        "weekly": DELIVERY_ROOT / "weekly_ranking.png",
        "focus": DELIVERY_ROOT / "top_etf_analysis.png",
        "divergence": DELIVERY_ROOT / "price_share_divergence.png",
        "daily10": MODULE_ROOT / "build" / "daily_top10.png",
    }
    directions = {key: DELIVERY_ROOT / "exploration" / f"direction_{key}_daily.png" for key in ("a", "b", "c")}
    try:
        first = True
        for scene, output in outputs.items():
            output.parent.mkdir(parents=True, exist_ok=True)
            url = f"http://127.0.0.1:{port}/web/index.html?scene={scene}&capture=1"
            run_agent_browser(["open", url], session)
            if first:
                run_agent_browser(["set", "viewport", "1080", "1920"], session)
                first = False
            run_agent_browser(["wait", "--fn", "window.__renderReady === true"], session)
            run_agent_browser(["screenshot", str(output)], session)
        for direction, output in directions.items():
            output.parent.mkdir(parents=True, exist_ok=True)
            url = f"http://127.0.0.1:{port}/web/index.html?scene=daily&direction={direction}&capture=1"
            run_agent_browser(["open", url], session)
            run_agent_browser(["wait", "--fn", "window.__renderReady === true"], session)
            run_agent_browser(["screenshot", str(output)], session)
    finally:
        try:
            run_agent_browser(["close"], session)
        except Exception:
            pass
        server.shutdown()
        server.server_close()
    overview = DELIVERY_ROOT / "design_overview.png"
    canv = Image.new("RGB", (4320, 1920), "#02060D")
    for index, key in enumerate(("daily", "weekly", "focus", "divergence")):
        with Image.open(outputs[key]).convert("RGB") as img:
            canv.paste(img, (index * 1080, 0))
    canv.save(overview, quality=95)
    comparison = DELIVERY_ROOT / "direction_comparison.png"
    comp = Image.new("RGB", (3240, 1920), "#02060D")
    for index, key in enumerate(("a", "b", "c")):
        with Image.open(directions[key]).convert("RGB") as img:
            comp.paste(img, (index * 1080, 0))
    comp.save(comparison, quality=95)
    return {**{k: str(v) for k, v in outputs.items()}, "design_overview": str(overview), "direction_comparison": str(comparison)}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="ETF short-video prototype pipeline")
    parser.add_argument("--date", help="trade date YYYY-MM-DD; defaults to latest")
    parser.add_argument("--render", choices=("data", "stills", "video", "all"), default="all")
    parser.add_argument("--voice", choices=("auto", "none"), default="auto")
    args = parser.parse_args()
    before_hash = file_sha256(DB_PATH)
    content, audit = build_content(args.date)
    run_dir = RUNS_ROOT / content["meta"]["run_id"]
    latest = RUNS_ROOT / "latest"
    write_json(run_dir / "content.json", content)
    write_json(run_dir / "audit.json", audit)
    write_json(latest / "content.json", content)
    write_json(latest / "audit.json", audit)
    outputs: dict[str, object] = {}
    if args.render in {"stills", "all"}:
        outputs["stills"] = render_stills(content)
    if args.render in {"video", "all"}:
        if not content["topic"]["publish"]:
            raise RuntimeError("选题评分或数据质量未达到发布门槛，已停止视频生成")
        if not (DELIVERY_ROOT / "daily_ranking.png").exists():
            outputs["stills"] = render_stills(content)
        outputs["video"] = render_video(DELIVERY_ROOT, content, load_json("render.json"), args.voice)
    after_hash = file_sha256(DB_PATH)
    manifest = {
        "schema_version": content["meta"]["schema_version"],
        "run_id": content["meta"]["run_id"],
        "trade_date": content["meta"]["trade_date"],
        "database_hash_before": before_hash,
        "database_hash_after": after_hash,
        "database_unchanged": before_hash == after_hash,
        "config_sha256": {p.name: sha256(p) for p in sorted((MODULE_ROOT / "config").glob("*")) if p.is_file()},
        "outputs": outputs,
    }
    files = [p for pattern in ("*.png", "*.mp4", "*.srt", "narration.txt") for p in DELIVERY_ROOT.glob(pattern)]
    manifest["output_sha256"] = {p.name: sha256(p) for p in files}
    write_json(run_dir / "manifest.json", manifest)
    write_json(latest / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if before_hash != after_hash:
        raise RuntimeError("etf.db hash changed during read-only pipeline")


if __name__ == "__main__":
    main()

