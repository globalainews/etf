from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .utils import atomic_json, sha256_file
from .validation import validate_business, validate_html_security, validate_schema
from .visual_qa import run_visual_qa


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import tts_engine


STAGES = ("prepare", "pages", "narration", "tts", "subtitles", "render", "concat", "qa", "finalize")
EXIT_CODES = {
    "prepare": 20,
    "pages": 30,
    "narration": 40,
    "tts": 50,
    "subtitles": 60,
    "render": 70,
    "concat": 80,
    "qa": 90,
    "finalize": 100,
}
VIDEO_W, VIDEO_H = 1080, 1920
FPS = 30
LEAD_PADDING = 0.45
TAIL_PADDING = 0.55
SUBTITLE_LINE_CHARS = 16
TTS_INSTRUCTION = (
    "You are a helpful assistant. Speak at a fast, natural pace like a financial news anchor. "
    "用清楚、自然、略快的中文财经播报语气朗读，ETF名称、数字和单位读完整。<|endofprompt|>"
)


class VideoBuildError(RuntimeError):
    def __init__(self, stage: str, message: str, code: int | None = None):
        super().__init__(message)
        self.stage = stage
        self.code = code or EXIT_CODES.get(stage, 1)


def sha(value: object) -> str:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        def encode_special(item: object) -> object:
            if isinstance(item, bytes):
                return {"__bytes_sha256__": hashlib.sha256(item).hexdigest()}
            if isinstance(item, Path):
                return str(item)
            raise TypeError(f"不支持计算结构哈希的类型：{type(item).__name__}")

        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=encode_special).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        return {}
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def run_command(command: list[str], stage: str, log: logging.Logger, timeout: int = 1800) -> None:
    log.info("command=%s", subprocess.list2cmdline(command))
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.stdout.strip():
        log.info("stdout=%s", result.stdout[-2000:].strip())
    if result.returncode:
        log.error("stderr=%s", result.stderr[-4000:].strip())
        raise VideoBuildError(stage, f"命令失败（{result.returncode}）：{command[0]}")


def replace_with_retry(source: Path, target: Path, attempts: int = 8) -> None:
    last: OSError | None = None
    for attempt in range(attempts):
        try:
            source.replace(target)
            return
        except OSError as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.2 * (attempt + 1))
    raise last or OSError(f"无法替换 {target}")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _spoken_units(text: str) -> list[str]:
    return [match.group(0) for match in re.finditer(r"[^。！？；]+[。！？；]?", text) if match.group(0).strip()]


def _split_at_comma(value: str) -> tuple[str, str] | None:
    positions = [index for index, char in enumerate(value) if char in "，、：,："]
    if not positions:
        return None
    midpoint = len(value) / 2
    point = min(positions, key=lambda index: abs(index - midpoint)) + 1
    if point < 3 or len(value) - point < 3:
        return None
    return value[:point], value[point:]


def split_spoken_chunks(text: str, target_count: int) -> list[str]:
    """Split the authoritative TTS text into one measured chunk per subtitle.

    Strong punctuation wins.  If the supplied subtitle plan is more granular,
    split the longest comma-delimited clause.  If it is less granular, merge
    the shortest adjacent pair.  The normalized source text is preserved.
    """
    chunks = _spoken_units(text)
    while len(chunks) < target_count:
        candidates = []
        for index, chunk in enumerate(chunks):
            split = _split_at_comma(chunk)
            if split:
                candidates.append((len(chunk), index, split))
        if not candidates:
            break
        _, index, pair = max(candidates)
        chunks[index:index + 1] = list(pair)
    while len(chunks) > target_count and len(chunks) > 1:
        index = min(range(len(chunks) - 1), key=lambda item: len(chunks[item]) + len(chunks[item + 1]))
        chunks[index:index + 2] = [chunks[index] + chunks[index + 1]]
    if len(chunks) != target_count:
        raise VideoBuildError(
            "narration",
            f"口播无法与字幕一一对齐：口播分句 {len(chunks)}，字幕 {target_count}",
        )
    if normalize_text("".join(chunks)) != normalize_text(text):
        raise VideoBuildError("narration", "口播分句改变了原始文本")
    return [chunk.strip() for chunk in chunks]


def subtitle_cards(text: str) -> list[str]:
    value = re.sub(r"\s+", "", text).strip()
    if not value:
        return []
    cards: list[str] = []
    while len(value) > SUBTITLE_LINE_CHARS * 2:
        limit = SUBTITLE_LINE_CHARS * 2
        positions = [i + 1 for i, char in enumerate(value[:limit]) if char in "，。；：、"]
        cut = max((point for point in positions if point >= 12), default=limit)
        cards.append(_wrap_subtitle(value[:cut]))
        value = value[cut:]
    cards.append(_wrap_subtitle(value))
    return cards


def _wrap_subtitle(value: str) -> str:
    if len(value) <= SUBTITLE_LINE_CHARS:
        return value
    low = max(1, len(value) - SUBTITLE_LINE_CHARS)
    high = min(SUBTITLE_LINE_CHARS, len(value) - 1)
    positions = [i + 1 for i, char in enumerate(value) if char in "，；：、"]
    candidates = [point for point in positions if low <= point <= high]
    cut = min(candidates, key=lambda point: abs(point - len(value) / 2)) if candidates else len(value) // 2
    cut = max(low, min(high, cut))
    return value[:cut].rstrip() + "\n" + value[cut:].lstrip()


def ass_time(seconds: float) -> str:
    centiseconds = round(max(seconds, 0) * 100)
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, centis = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def srt_time(seconds: float) -> str:
    milliseconds = round(max(seconds, 0) * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


@dataclass
class VideoContext:
    date: str
    input_root: Path = ROOT / "ETF"
    work_root: Path = ROOT / "cache" / "etf_longhubang_video"
    output_root: Path = ROOT / "output" / "ETF"
    schema_path: Path = ROOT / "docs" / "schemas" / "etf_longhubang.v1.schema.json"
    force: bool = False
    skip_cache: bool = False
    verbose: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"\d{8}", self.date):
            raise ValueError("日期必须为 YYYYMMDD")
        self.input = self.input_root / self.date
        self.work = self.work_root / self.date
        self.output = self.output_root / self.date
        self.manifest_path = self.work / "run_manifest.json"
        self.work.mkdir(parents=True, exist_ok=True)
        self.log = logging.getLogger(f"etf_longhubang_video.{self.date}")
        self.log.setLevel(logging.DEBUG if self.verbose else logging.INFO)
        self.log.handlers.clear()
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        for handler in (logging.FileHandler(self.work / "build_video.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)):
            handler.setFormatter(formatter)
            self.log.addHandler(handler)
        self.manifest = self._load_manifest()
        self._stage_keys: dict[str, str] = {}

    def _load_manifest(self) -> dict:
        if self.manifest_path.exists():
            try:
                return json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {
            "date": self.date,
            "stages": {},
            "started_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        }

    def save_manifest(self) -> None:
        self.manifest["updated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        atomic_json(self.manifest_path, self.manifest)

    def cached(self, stage: str, key: str, outputs: list[Path]) -> bool:
        self._stage_keys[stage] = key
        prior = self.manifest.get("stages", {}).get(stage, {})
        return (
            not self.force
            and not self.skip_cache
            and prior.get("status") == "success"
            and prior.get("key") == key
            and all(path.exists() for path in outputs)
        )

    def success(self, stage: str, outputs: list[Path], started: float) -> None:
        self.manifest.setdefault("stages", {})[stage] = {
            "status": "success",
            "key": self._stage_keys.get(stage, "runtime"),
            "seconds": round(time.monotonic() - started, 2),
            "outputs": [str(path) for path in outputs],
        }
        self.save_manifest()
        self.log.info("stage=%s status=success seconds=%.2f", stage, time.monotonic() - started)

    def failed(self, stage: str, message: str, started: float) -> None:
        self.manifest.setdefault("stages", {})[stage] = {
            "status": "failed",
            "seconds": round(max(0.0, time.monotonic() - started), 2),
            "error": message,
        }
        self.save_manifest()
        self.log.error("stage=%s status=failed error=%s", stage, message)


def _read_payload(ctx: VideoContext) -> dict:
    return json.loads((ctx.input / "etf_longhubang.json").read_text(encoding="utf-8"))


def stage_prepare(ctx: VideoContext) -> Path:
    output = ctx.work / "input_validation.json"
    required = [ctx.input / "_READY.json", ctx.input / "etf_longhubang.json"]
    if any(not path.exists() for path in required):
        raise VideoBuildError("prepare", f"素材包不完整：{ctx.input}")
    ready = json.loads(required[0].read_text(encoding="utf-8"))
    payload = _read_payload(ctx)
    html_files = [ctx.input / page["html_file"] for page in payload.get("pages", [])]
    if any(not path.exists() for path in html_files):
        missing = [path.name for path in html_files if not path.exists()]
        raise VideoBuildError("prepare", "缺少页面：" + "、".join(missing))
    key = sha([path.read_bytes() for path in required + html_files])
    if ctx.cached("prepare", key, [output]):
        return output
    if ready.get("content_type") != "etf_longhubang":
        raise VideoBuildError("prepare", "_READY content_type 错误")
    if ready.get("trade_date", "").replace("-", "") != ctx.date:
        raise VideoBuildError("prepare", "_READY 交易日与目录不一致")
    if payload.get("trade_date", "").replace("-", "") != ctx.date:
        raise VideoBuildError("prepare", "主 JSON 交易日与目录不一致")
    if ready.get("package_id") != payload.get("package_id"):
        raise VideoBuildError("prepare", "_READY 与主 JSON 的 package_id 不一致")
    if ready.get("main_file") != "etf_longhubang.json":
        raise VideoBuildError("prepare", "_READY main_file 错误")
    if ready.get("main_file_sha256") != sha256_file(ctx.input / "etf_longhubang.json"):
        raise VideoBuildError("prepare", "主 JSON SHA-256 校验失败")
    if ready.get("file_count") != 5:
        raise VideoBuildError("prepare", "_READY file_count 必须为 5")
    newest = max(path.stat().st_mtime_ns for path in [ctx.input / "etf_longhubang.json", *html_files])
    if (ctx.input / "_READY.json").stat().st_mtime_ns < newest:
        raise VideoBuildError("prepare", "_READY 不是素材包最后写入的文件")
    pages = {path.name: path.read_text(encoding="utf-8") for path in html_files}
    try:
        validate_schema(payload, ctx.schema_path)
        business = validate_business(payload, pages)
        security = validate_html_security(pages)
    except Exception as exc:
        raise VideoBuildError("prepare", str(exc)) from exc
    report = {
        "status": "ok",
        "date": ctx.date,
        "package_id": payload["package_id"],
        "main_file_sha256": ready["main_file_sha256"],
        "business": business,
        "html_security": security,
    }
    atomic_json(output, report)
    return output


def stage_pages(ctx: VideoContext) -> Path:
    output = ctx.work / "pages"
    payload = _read_payload(ctx)
    html_files = [page["html_file"] for page in payload["pages"]]
    expected = [output / f"{Path(name).stem}.png" for name in html_files]
    key = sha([ctx.input.joinpath(name).read_bytes() for name in html_files])
    if ctx.cached("pages", key, expected + [output / "visual_qa.json"]):
        return output
    if output.exists():
        shutil.rmtree(output)
    try:
        report = run_visual_qa(ctx.input, output, html_files)
    except Exception as exc:
        raise VideoBuildError("pages", str(exc)) from exc
    if report.get("status") != "ok" or any(not path.exists() for path in expected):
        raise VideoBuildError("pages", "页面渲染或视觉质检失败")
    return output


def stage_narration(ctx: VideoContext) -> Path:
    output = ctx.work / "narration.json"
    payload = _read_payload(ctx)
    key = sha(payload["pages"])
    if ctx.cached("narration", key, [output]):
        return output
    pages = []
    for page in payload["pages"]:
        subtitles = page["subtitle_sentences"]
        spoken = split_spoken_chunks(page["tts_text"], len(subtitles))
        chunks = [
            {"chunk_no": index, "tts_text": tts, "subtitle_text": subtitle}
            for index, (tts, subtitle) in enumerate(zip(spoken, subtitles), 1)
        ]
        pages.append(
            {
                "page_no": page["sequence"],
                "page_id": page["page_id"],
                "page_type": page["page_type"],
                "html_file": page["html_file"],
                "text": page["tts_text"],
                "chunks": chunks,
                "min_duration_sec": page["min_duration_sec"],
                "max_duration_sec": page["max_duration_sec"],
                "hash": sha(page["tts_text"]),
            }
        )
    atomic_json(output, {"date": ctx.date, "package_id": payload["package_id"], "pages": pages})
    return output


def _run_tts_batch(ctx: VideoContext, audio_dir: Path, items: list[dict]) -> None:
    input_path = audio_dir / "_tts_input.json"
    atomic_json(input_path, items)
    command = [
        tts_engine.COSYVOICE_PYTHON,
        tts_engine.COSYVOICE_SCRIPT,
        str(audio_dir),
        str(input_path),
        "--instruct-text",
        TTS_INSTRUCTION,
    ]
    for attempt in range(1, 4):
        ctx.log.info("stage=tts attempt=%s/3 chunks=%s", attempt, len(items))
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            if line.strip():
                ctx.log.info("stage=tts cosyvoice=%s", line.rstrip())
        code = process.wait()
        if code == 0:
            return
        if attempt < 3:
            time.sleep(2 ** (attempt - 1))
    raise VideoBuildError("tts", "Global CosyVoice3 连续三次生成失败")


def stage_tts(ctx: VideoContext) -> Path:
    output = ctx.work / "audio"
    output.mkdir(parents=True, exist_ok=True)
    narration = json.loads((ctx.work / "narration.json").read_text(encoding="utf-8"))
    key = sha({"narration": narration, "instruction": TTS_INSTRUCTION, "engine": "cosyvoice3-global-v1"})
    expected_pages = [output / f"page_{page['page_no']:02d}.wav" for page in narration["pages"]]
    if ctx.cached("tts", key, expected_pages + [output / "durations.json", output / "sentence_timings.json"]):
        return output
    pending: list[dict] = []
    for page in narration["pages"]:
        for chunk in page["chunks"]:
            chunk_id = f"p{page['page_no']:02d}_s{chunk['chunk_no']:02d}"
            wav = output / f"tts_{chunk_id}.wav"
            contract = output / f"tts_{chunk_id}.contract.sha256"
            wanted = sha(chunk["tts_text"] + "\n" + TTS_INSTRUCTION)
            if not (wav.exists() and contract.exists() and contract.read_text(encoding="utf-8").strip() == wanted and tts_engine.measure_audio_duration(str(wav)) > 0.1):
                pending.append({"index": chunk_id, "text": chunk["tts_text"]})
    if pending:
        _run_tts_batch(ctx, output, pending)
        by_id = {item["index"]: item["text"] for item in pending}
        for chunk_id, text in by_id.items():
            wav = output / f"tts_{chunk_id}.wav"
            if tts_engine.measure_audio_duration(str(wav)) <= 0.1:
                raise VideoBuildError("tts", f"无效音频：{wav.name}")
            (output / f"tts_{chunk_id}.contract.sha256").write_text(
                sha(text + "\n" + TTS_INSTRUCTION), encoding="utf-8"
            )
    durations: dict[str, float] = {}
    timings: dict[str, list[dict]] = {}
    for page in narration["pages"]:
        chunk_wavs: list[Path] = []
        rows: list[dict] = []
        cursor = 0.0
        for chunk in page["chunks"]:
            chunk_id = f"p{page['page_no']:02d}_s{chunk['chunk_no']:02d}"
            wav = output / f"tts_{chunk_id}.wav"
            duration = tts_engine.measure_audio_duration(str(wav))
            if duration <= 0.1:
                raise VideoBuildError("tts", f"缺少分句音频：{wav.name}")
            chunk_wavs.append(wav)
            rows.append(
                {
                    "chunk_no": chunk["chunk_no"],
                    "tts_text": chunk["tts_text"],
                    "subtitle_text": chunk["subtitle_text"],
                    "start": round(cursor, 3),
                    "end": round(cursor + duration, 3),
                }
            )
            cursor += duration
        page_id = f"page_{page['page_no']:02d}"
        concat_file = output / f"{page_id}_chunks.txt"
        concat_file.write_text("".join(f"file '{path.as_posix()}'\n" for path in chunk_wavs), encoding="utf-8")
        target = output / f"{page_id}.wav"
        temp = output / f".{page_id}.{os.getpid()}.tmp.wav"
        run_command(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(temp)],
            "tts",
            ctx.log,
            timeout=180,
        )
        replace_with_retry(temp, target)
        speech_duration = tts_engine.measure_audio_duration(str(target))
        page_duration = max(float(page["min_duration_sec"]), speech_duration + LEAD_PADDING + TAIL_PADDING)
        if page_duration > float(page["max_duration_sec"]) + 0.05:
            raise VideoBuildError(
                "tts",
                f"{page_id} 实际口播需要 {page_duration:.2f}s，超过页面上限 {page['max_duration_sec']}s",
            )
        durations[page_id] = round(page_duration, 3)
        timings[page_id] = rows
    atomic_json(output / "durations.json", durations)
    atomic_json(output / "sentence_timings.json", timings)
    return output


def stage_subtitles(ctx: VideoContext) -> Path:
    output = ctx.work / "subtitles"
    output.mkdir(parents=True, exist_ok=True)
    narration = json.loads((ctx.work / "narration.json").read_text(encoding="utf-8"))
    durations = json.loads((ctx.work / "audio" / "durations.json").read_text(encoding="utf-8"))
    timings = json.loads((ctx.work / "audio" / "sentence_timings.json").read_text(encoding="utf-8"))
    key = sha({"narration": narration, "durations": durations, "timings": timings, "style": "etf-v1"})
    expected = [output / f"page_{page['page_no']:02d}.ass" for page in narration["pages"]]
    if ctx.cached("subtitles", key, expected + [ctx.work / "subtitles.srt", ctx.work / "subtitle_plan.json"]):
        return output
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        "Style: Subtitle,Microsoft YaHei,50,&H00FFFFFF,&H00FFFFFF,&H00131820,&H90131820,-1,0,0,0,100,100,0,0,3,2,0,2,70,70,155,1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    plan: list[dict] = []
    global_events: list[dict] = []
    page_offset = 0.0
    for page in narration["pages"]:
        page_id = f"page_{page['page_no']:02d}"
        events: list[dict] = []
        for timing in timings[page_id]:
            cards = subtitle_cards(timing["subtitle_text"])
            if not cards:
                continue
            start = LEAD_PADDING + float(timing["start"])
            end = LEAD_PADDING + float(timing["end"])
            weights = [max(1, len(card.replace("\n", ""))) for card in cards]
            total = sum(weights)
            cursor = start
            for index, (card, weight) in enumerate(zip(cards, weights)):
                card_end = end if index == len(cards) - 1 else cursor + (end - start) * weight / total
                events.append({"text": card, "start": round(cursor, 3), "end": round(card_end, 3)})
                cursor = card_end
        dialogues = []
        for event in events:
            safe = event["text"].replace("\n", r"\N").replace("{", "（").replace("}", "）")
            dialogues.append(
                f"Dialogue: 0,{ass_time(event['start'])},{ass_time(event['end'])},Subtitle,,0,0,0,,{{\\fad(100,100)}}{safe}\n"
            )
            global_events.append(
                {"text": event["text"], "start": page_offset + event["start"], "end": page_offset + event["end"]}
            )
        (output / f"{page_id}.ass").write_text(header + "".join(dialogues), encoding="utf-8-sig")
        plan.append({"page_no": page["page_no"], "page_id": page["page_id"], "duration": durations[page_id], "segments": events})
        page_offset += float(durations[page_id])
    srt = "".join(
        f"{index}\n{srt_time(event['start'])} --> {srt_time(event['end'])}\n{event['text']}\n\n"
        for index, event in enumerate(global_events, 1)
    )
    (ctx.work / "subtitles.srt").write_text(srt, encoding="utf-8-sig")
    atomic_json(ctx.work / "subtitle_plan.json", {"date": ctx.date, "pages": plan})
    return output


def stage_render(ctx: VideoContext) -> Path:
    output = ctx.work / "segments"
    output.mkdir(parents=True, exist_ok=True)
    narration = json.loads((ctx.work / "narration.json").read_text(encoding="utf-8"))
    durations = json.loads((ctx.work / "audio" / "durations.json").read_text(encoding="utf-8"))
    images = [ctx.work / "pages" / Path(page["html_file"]).with_suffix(".png").name for page in narration["pages"]]
    subtitles = [ctx.work / "subtitles" / f"page_{page['page_no']:02d}.ass" for page in narration["pages"]]
    key = sha(
        {
            "images": [sha256_file(path) for path in images],
            "audio": [sha256_file(ctx.work / "audio" / f"page_{page['page_no']:02d}.wav") for page in narration["pages"]],
            "subtitles": [sha256_file(path) for path in subtitles],
            "durations": durations,
            "renderer": "etf-static-card-v1",
        }
    )
    expected = [output / f"page_{page['page_no']:02d}.mp4" for page in narration["pages"]]
    if ctx.cached("render", key, expected):
        return output
    for page, image, ass, target in zip(narration["pages"], images, subtitles, expected):
        page_id = f"page_{page['page_no']:02d}"
        audio = ctx.work / "audio" / f"{page_id}.wav"
        duration = float(durations[page_id])
        temp = target.with_suffix(".tmp.mp4")
        ass_path = str(ass).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        fade_out = max(0.1, duration - 0.18)
        video_filter = (
            f"scale={VIDEO_W}:{VIDEO_H}:flags=lanczos,format=yuv420p,"
            f"fade=t=in:st=0:d=0.18,fade=t=out:st={fade_out:.3f}:d=0.18,subtitles='{ass_path}'"
        )
        audio_filter = (
            f"adelay={int(LEAD_PADDING * 1000)}|{int(LEAD_PADDING * 1000)},"
            f"apad=whole_dur={duration:.3f},atrim=duration={duration:.3f}"
        )
        run_command(
            [
                "ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(image), "-i", str(audio),
                "-vf", video_filter, "-af", audio_filter, "-t", f"{duration:.3f}", "-r", str(FPS),
                "-c:v", "libx264", "-profile:v", "high", "-level", "4.1", "-crf", "18", "-preset", "medium",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart", str(temp),
            ],
            "render",
            ctx.log,
            timeout=900,
        )
        replace_with_retry(temp, target)
    return output


def stage_concat(ctx: VideoContext) -> Path:
    output = ctx.work / f"ETF龙虎榜_{ctx.date}.mp4"
    narration = json.loads((ctx.work / "narration.json").read_text(encoding="utf-8"))
    segments = [ctx.work / "segments" / f"page_{page['page_no']:02d}.mp4" for page in narration["pages"]]
    key = sha([sha256_file(path) for path in segments])
    if ctx.cached("concat", key, [output]):
        return output
    concat_file = ctx.work / "concat.txt"
    concat_file.write_text("".join(f"file '{path.as_posix()}'\n" for path in segments), encoding="utf-8")
    temp = output.with_suffix(".tmp.mp4")
    run_command(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", "-movflags", "+faststart", str(temp)],
        "concat",
        ctx.log,
        timeout=300,
    )
    replace_with_retry(temp, output)
    return output


def stage_qa(ctx: VideoContext) -> Path:
    video = ctx.work / f"ETF龙虎榜_{ctx.date}.mp4"
    output = ctx.work / "qa_report.json"
    visual = json.loads((ctx.work / "pages" / "visual_qa.json").read_text(encoding="utf-8"))
    durations = json.loads((ctx.work / "audio" / "durations.json").read_text(encoding="utf-8"))
    key = sha({"video": sha256_file(video), "visual": visual, "durations": durations})
    if ctx.cached("qa", key, [output]):
        return output
    info = probe(video)
    streams = info.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    duration = float(info.get("format", {}).get("duration", 0) or 0)
    expected_duration = sum(float(value) for value in durations.values())
    issues: list[str] = []
    if video.stat().st_size < 200_000:
        issues.append("video_too_small")
    if [video_stream.get("width"), video_stream.get("height")] != [VIDEO_W, VIDEO_H]:
        issues.append("wrong_resolution")
    if video_stream.get("codec_name") != "h264":
        issues.append("wrong_video_codec")
    if audio_stream.get("codec_name") != "aac":
        issues.append("missing_or_invalid_audio")
    if abs(duration - expected_duration) > 0.35:
        issues.append("duration_mismatch")
    if not 45 <= duration <= 90:
        issues.append("duration_outside_short_video_budget")
    if visual.get("status") != "ok":
        issues.append("page_visual_qa_failed")
    black = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video), "-vf", "blackdetect=d=0.25:pix_th=0.02:pic_th=0.98", "-an", "-f", "null", "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if "black_start:" in black.stderr:
        issues.append("black_frame_detected")
    volume = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video), "-vn", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if "mean_volume: -inf" in volume.stderr:
        issues.append("silent_audio")
    report = {
        "status": "ok" if not issues else "failed",
        "date": ctx.date,
        "video": str(video),
        "bytes": video.stat().st_size,
        "duration_seconds": round(duration, 3),
        "expected_duration_seconds": round(expected_duration, 3),
        "resolution": [video_stream.get("width"), video_stream.get("height")],
        "video_codec": video_stream.get("codec_name"),
        "audio_codec": audio_stream.get("codec_name"),
        "automated_checks": {
            "input_contract": "ok",
            "page_visual_qa": visual.get("status"),
            "black_frame_scan": "black_start:" not in black.stderr,
            "audio_non_silent": "mean_volume: -inf" not in volume.stderr,
        },
        "manual_checks_required": ["配音自然度", "ETF专名读音", "字幕与口播同步", "页面与口播语义匹配"],
        "issues": issues,
    }
    atomic_json(output, report)
    if issues:
        raise VideoBuildError("qa", "自动质检失败：" + "、".join(issues))
    return output


def stage_finalize(ctx: VideoContext) -> Path:
    final = ctx.output / f"ETF龙虎榜_{ctx.date}.mp4"
    qa = ctx.work / "qa_report.json"
    key = sha(qa.read_text(encoding="utf-8"))
    sidecars = ("narration.json", "subtitle_plan.json", "subtitles.srt", "qa_report.json", "input_validation.json")
    cover = ctx.output / f"ETF龙虎榜_{ctx.date}_cover.jpg"
    if ctx.cached("finalize", key, [final, cover, *[ctx.output / name for name in sidecars]]):
        return final
    ctx.output.mkdir(parents=True, exist_ok=True)
    temp = final.with_suffix(".tmp.mp4")
    shutil.copy2(ctx.work / f"ETF龙虎榜_{ctx.date}.mp4", temp)
    replace_with_retry(temp, final)
    source_cover = ctx.work / "pages" / "page_01_cover.png"
    temp_cover = cover.with_suffix(".tmp.jpg")
    run_command(["ffmpeg", "-y", "-i", str(source_cover), "-frames:v", "1", "-q:v", "2", str(temp_cover)], "finalize", ctx.log, timeout=120)
    replace_with_retry(temp_cover, cover)
    for name in sidecars:
        shutil.copy2(ctx.work / name, ctx.output / name)
    shutil.copy2(ctx.input / "etf_longhubang.json", ctx.output / "etf_longhubang.json")
    return final


HANDLERS = {
    "prepare": stage_prepare,
    "pages": stage_pages,
    "narration": stage_narration,
    "tts": stage_tts,
    "subtitles": stage_subtitles,
    "render": stage_render,
    "concat": stage_concat,
    "qa": stage_qa,
    "finalize": stage_finalize,
}


def build(ctx: VideoContext, from_stage: str | None = None, to_stage: str | None = None, dry_run: bool = False) -> dict:
    first = STAGES.index(from_stage) if from_stage else 0
    last = STAGES.index(to_stage) if to_stage else len(STAGES) - 1
    if first > last:
        raise VideoBuildError("prepare", "from-stage 不能晚于 to-stage")
    started = time.monotonic()
    outputs: dict[str, str] = {}
    for stage in STAGES[first:last + 1]:
        tick = time.monotonic()
        ctx.log.info("stage=%s status=start", stage)
        if dry_run:
            ctx.log.info("stage=%s status=dry-run", stage)
            continue
        try:
            result = HANDLERS[stage](ctx)
            outputs[stage] = str(result)
            ctx.success(stage, [Path(result)], tick)
        except VideoBuildError as exc:
            ctx.failed(stage, str(exc), tick)
            raise
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            ctx.failed(stage, message, tick)
            raise VideoBuildError(stage, message) from exc
    if not dry_run and last == len(STAGES) - 1 and (ctx.output / f"ETF龙虎榜_{ctx.date}.mp4").exists():
        shutil.copy2(ctx.manifest_path, ctx.output / "run_manifest.json")
    return {"outputs": outputs, "seconds": round(time.monotonic() - started, 2)}
