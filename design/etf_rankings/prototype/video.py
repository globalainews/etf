from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path


def write_srt(content: dict, path: Path) -> None:
    lines = []
    for index, scene in enumerate(content["scenes"], 1):
        def stamp(seconds: float) -> str:
            whole = int(seconds)
            return f"00:00:{whole:02d},000"
        lines.extend([str(index), f"{stamp(scene['start'])} --> {stamp(scene['end'])}", scene["voice"], ""])
    path.write_text("\n".join(lines), encoding="utf-8")


async def _edge_tts(text: str, voice: str, output: Path) -> None:
    import edge_tts
    await edge_tts.Communicate(text, voice=voice, rate="-4%", volume="+0%").save(str(output))


def create_voice(content: dict, config: dict, output: Path, mode: str) -> tuple[bool, str]:
    if mode == "none":
        return False, "voice disabled"
    text = "\n".join(scene["voice"] for scene in content["scenes"])
    for voice in (config["voice_primary"], config["voice_fallback"]):
        try:
            asyncio.run(asyncio.wait_for(_edge_tts(text, voice, output), timeout=15))
            if output.exists() and output.stat().st_size > 1000:
                return True, voice
        except Exception as exc:
            last_error = f"{voice}: {exc}"
    return False, locals().get("last_error", "edge-tts unavailable")


def render_video(root: Path, content: dict, config: dict, voice_mode: str = "auto") -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    build = root / "prototype" / "build"
    build.mkdir(parents=True, exist_ok=True)
    narration = build / "narration.mp3"
    srt = root / "narration.srt"
    narration_text = root / "narration.txt"
    write_srt(content, srt)
    narration_text.write_text("\n".join(scene["voice"] for scene in content["scenes"]) + "\n", encoding="utf-8")
    has_voice, voice_status = create_voice(content, config, narration, voice_mode)

    daily = root / "daily_ranking.png"
    focus = root / "top_etf_analysis.png"
    ending = root / "prototype" / "build" / "daily_top10.png"
    for path in (daily, focus, ending):
        if not path.exists():
            raise FileNotFoundError(path)
    output = root / "etf_ranking_demo.mp4"
    filter_video = (
        "[0:v]scale=1080:1920,fps=30,trim=duration=12,setpts=PTS-STARTPTS,setsar=1[v0];"
        "[1:v]scale=1080:1920,fps=30,trim=duration=9,setpts=PTS-STARTPTS,setsar=1[v1];"
        "[2:v]scale=1080:1920,fps=30,trim=duration=5,setpts=PTS-STARTPTS,setsar=1[v2];"
        "[v0][v1]xfade=transition=fade:duration=0.5:offset=11.5[x1];"
        "[x1][v2]xfade=transition=fade:duration=0.5:offset=20.0[vout]"
    )
    command = [ffmpeg, "-y", "-loop", "1", "-t", "12", "-i", str(daily), "-loop", "1", "-t", "9", "-i", str(focus), "-loop", "1", "-t", "5", "-i", str(ending)]
    if has_voice:
        command += ["-i", str(narration)]
        audio_index = 3
    else:
        command += ["-f", "lavfi", "-t", "25", "-i", "anullsrc=r=48000:cl=stereo"]
        audio_index = 3
    command += [
        "-filter_complex", filter_video,
        "-map", "[vout]", "-map", f"{audio_index}:a",
        "-af", "apad=pad_dur=25,atrim=0:25,loudnorm=I=-16:TP=-1.5:LRA=11",
        "-t", "25", "-r", "30", "-c:v", config["video_codec"], "-preset", "fast", "-crf", "19",
        "-pix_fmt", config["pixel_format"], "-c:a", config["audio_codec"], "-ar", str(config["audio_rate"]), "-b:a", "160k", "-movflags", "+faststart", str(output)
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    return {"path": str(output), "voice": voice_status, "has_voice": has_voice, "ffmpeg_command": command}

