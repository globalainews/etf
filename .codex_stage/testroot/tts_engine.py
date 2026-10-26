#!/usr/bin/env python3
"""
tts_engine.py — TTS 引擎模块（从 build-video.py 提取）

为环球头条简读流水线提供 CosyVoice3 语音合成、音频组装、音效生成等功能。

用法（模块）:
    from tts_engine import generate_all_tts, ...
    generate_all_tts(project)

用法（CLI）:
    python tts_engine.py <audio_dir> [--tts-only] [--dry-run]

依赖:
    - CosyVoice3 conda 环境 (F:/anaconda3/envs/cosyvoice/python.exe)
    - ffmpeg / ffprobe
    - PIL/Pillow（ensure_flip_sound 回退时）
"""

import os
import sys
import json
import subprocess
import shutil
import time
import hashlib
import re
from pathlib import Path
from typing import List, Optional

# Fix Windows GBK encoding issues with emoji/special chars
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Constants ──────────────────────────────────────────────────────────────

# CosyVoice3 configuration (standalone script run in cosyvoice conda env)
COSYVOICE_PYTHON = "F:/anaconda3/envs/cosyvoice/python.exe"
COSYVOICE_SCRIPT = "D:/antigravity/tools/cosyvoice_tts.py"
TTS_VOICE = "zh-CN-XiaoxiaoNeural"  # edge-tts voice (NOT used — CosyVoice3 is primary)
TTS_RATE = "+10%"          # faster than natural for short-video pacing
TTS_RETRIES = 3
TTS_RETRY_DELAY_BASE = 1.0  # seconds, exponential backoff
TTS_CALL_GAP = 1.0          # seconds between TTS calls to avoid rate-limiting

# Flip sound source
FLIP_SOUND_PATH = "D:/antigravity/assets/flip.wav"
FLIP_SOUND_DUR = 0.4       # page-turn sound effect duration

# Silence and norm caches
SILENCE_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "silence")
NORM_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "_norm_cache")

# Audio format targets — all concat pipeline files must be 48kHz, 2ch, pcm_s16le
AUDIO_TARGET_CODEC = "pcm_s16le"
AUDIO_TARGET_SR = "48000"
AUDIO_TARGET_CH = "2"

# Short version CTA (membership call-to-action)
CTA_VOICEOVER = "支持小编看完整视频，欢迎加入会员专区"
CTA_TTS_ID = "cta"
CTA_MIN_DURATION = 4.5     # floor for the CTA page

FLASH_TAIL_AFTER_SPEECH = 0.15
FLASH_SILENCE_THRESHOLD_DB = -45
FLASH_SILENCE_MIN_DURATION = 0.08

# ── Data Class Imports ─────────────────────────────────────────────────────
# VideoProject and PageConfig are defined in build-video.py; they are passed
# in as dict-like objects with the following keys/attributes:
#   - project.audio_dir: str
#   - project.short_version: bool
#   - project.cta_text: str
#   - project.pages: list of PageConfig objects
# Each PageConfig has: index, voiceover_text, tts_id, is_flash

# ── Data Classes (standalone for CLI usage) ─────────────────────────────────

from dataclasses import dataclass, field
from typing import List as TypingList, Tuple

@dataclass
class PageConfig:
    """Minimal page config for TTS engine."""
    tts_id: str = ""
    voiceover_text: str = ""
    is_flash: bool = False

# ── Low-level Utilities ─────────────────────────────────────────────────────

def text_hash(text: str) -> str:
    """Short hash of text for cache validation."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:8]


def measure_audio_duration(audio_path: str) -> float:
    """Use ffprobe to get audio duration in seconds."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             audio_path],
            capture_output=True, text=True, encoding="utf-8"
        )
        return float(result.stdout.strip())
    except (ValueError, subprocess.SubprocessError) as e:
        print(f"  [WARN] ffprobe failed for {audio_path}: {e}")
        return 0.0


def ensure_norm_audio(path: str) -> str:
    """Ensure a file is 48kHz, 2ch, pcm_s16le. Returns path (may be cached).

    Concat demuxer requires ALL files to have identical format. CosyVoice3
    outputs pcm_f32le mono WAV which MUST be converted before concat,
    otherwise the raw bytes get misinterpreted as s16le stereo → garbage/noise.
    """
    os.makedirs(NORM_CACHE_DIR, exist_ok=True)

    if not os.path.exists(path) or os.path.getsize(path) < 1024:
        return path

    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name,sample_rate,channels,sample_fmt,bits_per_sample",
             "-of", "default=noprint_wrappers=1:nokey=1",
             path],
            capture_output=True, text=True, timeout=10,
        )
        info = r.stdout.strip()
        expected = f"{AUDIO_TARGET_CODEC}|{AUDIO_TARGET_SR}|{AUDIO_TARGET_CH}|s16|16"
        if info == expected:
            return path
    except Exception:
        pass

    # Need conversion — cache key must include source file identity (path + mtime + size)
    try:
        _st = os.stat(path)
        _ident = f"{path}:{_st.st_mtime:.6f}:{_st.st_size}"
    except Exception:
        _ident = path
    safe_name = hashlib.md5(_ident.encode("utf-8")).hexdigest()[:12]
    cache_file = os.path.join(NORM_CACHE_DIR, f"{safe_name}.wav")
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 1024:
        return cache_file

    print(f"  Converting {os.path.basename(path)} → {AUDIO_TARGET_CODEC}/{AUDIO_TARGET_SR}Hz/{AUDIO_TARGET_CH}ch")
    subprocess.run([
        "ffmpeg", "-y", "-i", path,
        "-ar", AUDIO_TARGET_SR, "-ac", AUDIO_TARGET_CH,
        "-c:a", AUDIO_TARGET_CODEC, cache_file,
    ], capture_output=True, check=True)
    return cache_file


def _ensure_silence_file(dur_ms: int) -> str:
    """Generate or retrieve a globally-cached silence WAV file."""
    os.makedirs(SILENCE_CACHE_DIR, exist_ok=True)
    name = f"silence_{dur_ms}ms_{AUDIO_TARGET_SR}.wav"
    path = os.path.join(SILENCE_CACHE_DIR, name)
    if os.path.exists(path) and os.path.getsize(path) > 256:
        return path
    dur_sec = dur_ms / 1000.0
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"anullsrc=r={AUDIO_TARGET_SR}:cl=stereo:d={dur_sec:.3f}",
        "-c:a", "pcm_s16le",
        path
    ], capture_output=True, check=True)
    return path


# ── Core TTS Engine ────────────────────────────────────────────────────────

def generate_cosyvoice_batch(audio_dir: str, items: list) -> dict:
    """
    Generate TTS for all pages via standalone CosyVoice3 script.
    Runs in the cosyvoice conda env to avoid dependency conflicts.
    Returns dict mapping tts_id (str) → duration_seconds.
    Retries up to TTS_RETRIES times on GPU crash (exit code 0x8xxxxxxx).
    Exits with error if CosyVoice3 fails after all retries.
    """
    # Write input JSON
    json_path = os.path.join(audio_dir, "_tts_input.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)

    print(f"  [CosyVoice3] Batch generating {len(items)} TTS files...")
    cmd = [COSYVOICE_PYTHON, COSYVOICE_SCRIPT, audio_dir, json_path]

    for attempt in range(1, TTS_RETRIES + 1):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=1200,  # 20 min for batch of 7 (model load ~60s + ~60s per page)
                env={**os.environ}
            )

            # Print stderr (progress messages)
            stderr = result.stderr or ""
            stderr_lines = stderr.strip().split("\n")
            for line in stderr_lines:
                if line.strip():
                    print(f"    {line.strip()}")

            if result.returncode != 0:
                # GPU crash (Windows non-zero exit like 0x80000005, 0xC0000005, etc.)
                if attempt < TTS_RETRIES:
                    delay = TTS_RETRY_DELAY_BASE ** attempt
                    print(f"  [WARN] CosyVoice3 batch failed with code {result.returncode} (attempt {attempt}/{TTS_RETRIES}), retrying in {delay:.0f}s...")
                    time.sleep(delay)
                    continue
                print(f"  [ERROR] CosyVoice3 batch failed with code {result.returncode} after {TTS_RETRIES} attempts")
                print(f"  [ERROR] stderr: {stderr[-500:] if stderr else '(empty)'}")
                sys.exit(1)

            # Parse stdout: look for "OK N dur0,dur1,..." line
            stdout = result.stdout or ""
            stdout = stdout.strip()
            ok_line = None
            for line in stdout.split("\n"):
                line = line.strip()
                if line.startswith("OK "):
                    ok_line = line
                    break

            if not ok_line:
                print(f"  [ERROR] CosyVoice3: no OK line in output")
                print(f"  stdout: {stdout[-500:] if stdout else '(empty)'}")
                sys.exit(1)

            parts = ok_line.split()
            durations_str = parts[2] if len(parts) > 2 else ""
            # Key by the caller's own id (items[i]["index"]), NOT the batch position.
            durations = {}
            for i, dur in enumerate(durations_str.split(",")):
                if i < len(items):
                    durations[str(items[i]["index"])] = float(dur)

            if attempt > 1:
                print(f"  [CosyVoice3] Batch succeeded on attempt {attempt}: {len(durations)} files")
            else:
                print(f"  [CosyVoice3] Batch complete: {len(durations)} files")
            return durations

        except subprocess.TimeoutExpired:
            if attempt < TTS_RETRIES:
                delay = TTS_RETRY_DELAY_BASE ** attempt
                print(f"  [WARN] CosyVoice3 batch timed out (attempt {attempt}/{TTS_RETRIES}), retrying in {delay:.0f}s...")
                time.sleep(delay)
                continue
            print(f"  [ERROR] CosyVoice3 batch timed out after {TTS_RETRIES} attempts")
            sys.exit(1)
        except Exception as e:
            if attempt < TTS_RETRIES:
                delay = TTS_RETRY_DELAY_BASE ** attempt
                print(f"  [WARN] CosyVoice3 batch failed: {e} (attempt {attempt}/{TTS_RETRIES}), retrying in {delay:.0f}s...")
                time.sleep(delay)
                continue
            print(f"  [ERROR] CosyVoice3 batch failed after {TTS_RETRIES} attempts: {e}")
            sys.exit(1)


def ensure_flip_sound() -> str:
    """Ensure a 24kHz PCM WAV page-turn effect exists. Returns path.

    使用 assets/flip.wav 作为翻页音效。必须统一转为 24kHz PCM 16-bit 立体声
    后才能用于 concat（TTS 管道全是 24kHz，格式不匹配会输出噪音）。
    flip.wav 缺失时才回退生成粉噪翻页声。
    """
    from PIL import Image, ImageDraw, ImageFont  # noqa: F401

    def _fallback():
        """Generate pink-noise page-turn at 48kHz."""
        print(f"  Generating page-turn sound effect...")
        dur = FLIP_SOUND_DUR
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"anoisesrc=d={dur}:c=pink:a=0.4",
            "-af",
            f"afade=t=in:d=0.01,"
            f"afade=t=out:st={dur-0.08}:d=0.08,"
            f"equalizer=f=800:t=q:w=2:g=8,"
            f"equalizer=f=3000:t=q:w=3:g=-4,"
            f"volume=0.35",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
            FLIP_SOUND_PATH
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        print(f"  Flip sound generated: {FLIP_SOUND_PATH}")
        return FLIP_SOUND_PATH

    if not (os.path.exists(FLIP_SOUND_PATH) and os.path.getsize(FLIP_SOUND_PATH) > 1024):
        return _fallback()

    # Check sample rate: must be 24kHz, resample if not
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate",
             "-of", "default=noprint_wrappers=1:nokey=1",
             FLIP_SOUND_PATH],
            capture_output=True, text=True, timeout=10,
        )
        sr = r.stdout.strip()
        if sr == "48000":
            print(f"  Flip sound ready: {FLIP_SOUND_PATH} (48kHz)")
            return FLIP_SOUND_PATH
        print(f"  Flip sound {sr}Hz → resample to 48kHz for concat")
        tmp = FLIP_SOUND_PATH + ".tmp48k.wav"
        subprocess.run([
            "ffmpeg", "-y", "-i", FLIP_SOUND_PATH,
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
            tmp,
        ], capture_output=True, check=True)
        shutil.move(tmp, FLIP_SOUND_PATH)
        print(f"  Flip sound resampled: {FLIP_SOUND_PATH} (48kHz)")
        return FLIP_SOUND_PATH
    except Exception:
        print(f"  [WARN] Could not probe flip sound, generating new one")
        return _fallback()


# ── High-level Orchestration ────────────────────────────────────────────────

def generate_all_tts(project) -> None:
    """Generate TTS for all pages using CosyVoice3 voice cloning (batch mode).

    Args:
        project: Object with attributes:
            - audio_dir: str
            - short_version: bool
            - pages: list of PageConfig objects
    """
    os.makedirs(project.audio_dir, exist_ok=True)

    # Collect which pages need TTS (check cache first)
    need_tts = []
    for page in project.pages:
        if not page.voiceover_text.strip():
            print(f"  [SKIP] Page {page.tts_id}: no voiceover (silent page)")
            continue
        audio_path = os.path.join(project.audio_dir, f"tts_{page.tts_id}.wav")
        hash_file = audio_path + ".txt"
        current_hash = text_hash(page.voiceover_text)

        # Check cache
        if os.path.exists(audio_path) and os.path.exists(hash_file):
            with open(hash_file, "r", encoding="utf-8") as f:
                cached_hash = f.read().strip()
            if cached_hash == current_hash:
                duration = measure_audio_duration(audio_path)
                if duration > 0.1:
                    print(f"  [CACHE] Page {page.tts_id}: using cached TTS ({duration:.1f}s)")
                    continue

        # Flash voiceover text differs between long/short runs; its .rawdur marker
        # goes stale on regen.
        if getattr(page, "is_flash", False):
            marker = audio_path + ".rawdur"
            if os.path.exists(marker):
                os.remove(marker)

        print(f"  [TTS] Page {page.tts_id}: needs generation ({len(page.voiceover_text)} chars)")
        # Log hook for debugging
        if page.tts_id == "0":
            print(f"  [HOOK] Page 0: '{page.voiceover_text[:40]}...'")
        need_tts.append({"index": page.tts_id, "text": page.voiceover_text})

    if not need_tts:
        print("  All TTS cached, skipping generation")
        return

    # Batch generate via CosyVoice3
    durations = generate_cosyvoice_batch(project.audio_dir, need_tts)

    # Write cache hash files for newly generated pages
    for page in project.pages:
        if page.tts_id in durations:
            hash_file = os.path.join(project.audio_dir, f"tts_{page.tts_id}.wav.txt")
            with open(hash_file, "w", encoding="utf-8") as f:
                f.write(text_hash(page.voiceover_text))


def generate_cta_tts(project) -> None:
    """Generate TTS for the membership CTA tail page."""
    # 2026-08-18 起会员 CTA 尾页已移除（CTA 移到快讯页口播），cta_text 为空即不生成；
    # 原 `or CTA_VOICEOVER` 回退会让长版本（cta_text="")误加会员口播。
    cta_text = getattr(project, "cta_text", "")
    if not cta_text:
        return
    audio_path = os.path.join(project.audio_dir, f"tts_{CTA_TTS_ID}.wav")
    hash_file = audio_path + ".txt"
    current_hash = text_hash(cta_text)

    # Check cache
    if os.path.exists(audio_path) and os.path.exists(hash_file):
        with open(hash_file, "r", encoding="utf-8") as f:
            if f.read().strip() == current_hash:
                dur = measure_audio_duration(audio_path)
                if dur > 0.1:
                    print(f"  [CACHE] CTA end page: using cached TTS ({dur:.1f}s)")
                    return

    print(f"  [TTS] CTA end page: generating ({len(cta_text)} chars)")
    items = [{"index": CTA_TTS_ID, "text": cta_text}]
    durations = generate_cosyvoice_batch(project.audio_dir, items)

    if CTA_TTS_ID in durations:
        with open(hash_file, "w", encoding="utf-8") as f:
            f.write(current_hash)


def _trailing_silence_start(audio_path: str) -> float:
    """Return the start of trailing silence, or the file duration if none exists."""
    duration = measure_audio_duration(audio_path)
    if duration <= 0:
        return duration
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", audio_path,
        "-af", (
            f"silencedetect=noise={FLASH_SILENCE_THRESHOLD_DB}dB:"
            f"d={FLASH_SILENCE_MIN_DURATION:.3f}"
        ),
        "-f", "null", "-",
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    starts = re.findall(r"silence_start:\s*([0-9.]+)", result.stderr)
    ends = re.findall(r"silence_end:\s*([0-9.]+)", result.stderr)
    if starts and ends and abs(float(ends[-1]) - duration) <= 0.10:
        return min(float(starts[-1]), duration)
    return duration


def _terminal_flash(project):
    if not project.pages or getattr(project, "cta_text", ""):
        return None
    page = project.pages[-1]
    return page if getattr(page, "is_flash", False) and page.voiceover_text.strip() else None


def _terminal_end_pause_ms(project, default_pause_ms: int) -> int:
    return 0 if _terminal_flash(project) else default_pause_ms


def pad_flash_tts(project) -> None:
    """Normalize a terminal flash TTS to 150 ms after its last audible speech."""
    flash_page = _terminal_flash(project)
    if flash_page is None:
        return
    tts_path = os.path.join(project.audio_dir, f"tts_{flash_page.tts_id}.wav")
    if not os.path.exists(tts_path):
        return
    legacy_marker = tts_path + ".rawdur"
    speech_end = _trailing_silence_start(tts_path)
    target = speech_end + FLASH_TAIL_AFTER_SPEECH
    cur = measure_audio_duration(tts_path)
    if abs(cur - target) <= 0.02:
        if os.path.exists(legacy_marker):
            os.remove(legacy_marker)
        print(f"  News-flash TTS already ends {FLASH_TAIL_AFTER_SPEECH:.2f}s after speech ({cur:.3f}s)")
        return
    tmp = tts_path + ".tail.wav"
    subprocess.run([
        "ffmpeg", "-y", "-i", tts_path,
        "-af", (
            f"apad=pad_dur={FLASH_TAIL_AFTER_SPEECH:.3f},"
            f"atrim=end={target:.3f},asetpts=N/SR/TB"
        ),
        "-c:a", AUDIO_TARGET_CODEC, "-ar", AUDIO_TARGET_SR, "-ac", AUDIO_TARGET_CH, tmp,
    ], capture_output=True, check=True)
    os.replace(tmp, tts_path)
    if os.path.exists(legacy_marker):
        os.remove(legacy_marker)
    new_dur = measure_audio_duration(tts_path)
    action = "trimmed" if cur > target else "padded"
    print(f"  News-flash TTS {action}: {cur:.3f}s -> {new_dur:.3f}s "
          f"(speech end {speech_end:.3f}s + tail {FLASH_TAIL_AFTER_SPEECH:.2f}s)")


# ── Voiceover Assembly ──────────────────────────────────────────────────────

def assemble_voiceover(project, PAUSE_AFTER_TTS=0.3, TRANSITION_DUR=0.6, END_PAGE_DURATION=0.5, MIN_PAGE_DURATION=2.5) -> str:
    """
    Assemble the full voiceover track using ffmpeg concat demuxer.

    Timeline: [TTS_0] [pre_gap] [FLIP] [post_gap] [TTS_1] ... [TTS_6] [end_pause]

    Args:
        project: VideoProject-like object
        PAUSE_AFTER_TTS: silence after each TTS before flip (default 0.3)
        TRANSITION_DUR: total transition duration (default 0.6)
        END_PAGE_DURATION: end screen duration (default 0.5)
        MIN_PAGE_DURATION: minimum page display time (default 2.5)

    Returns:
        Path to assembled 48kHz WAV file.
    """
    flip_path = ensure_flip_sound()

    # Gap durations in ms
    pre_gap_ms = int(PAUSE_AFTER_TTS * 1000)
    flip_dur = measure_audio_duration(flip_path) or FLIP_SOUND_DUR
    actual_flip_ms = int(flip_dur * 1000)
    # Silence before/after flip to fill TRANSITION_DUR
    post_gap_ms = max(0, int(TRANSITION_DUR * 1000) - actual_flip_ms)
    # Trailing breath after the last voiced page
    if getattr(project, "short_version", False):
        end_pause_ms = int(PAUSE_AFTER_TTS * 1000)
    else:
        end_pause_ms = int((PAUSE_AFTER_TTS + TRANSITION_DUR + END_PAGE_DURATION + 0.2) * 1000)
    end_pause_ms = _terminal_end_pause_ms(project, end_pause_ms)

    silence_pre = _ensure_silence_file(pre_gap_ms)
    silence_post = _ensure_silence_file(post_gap_ms)
    silence_end = _ensure_silence_file(end_pause_ms) if end_pause_ms else None

    print(f"  Audio: TTS | {pre_gap_ms}ms pause | {actual_flip_ms}ms flip({os.path.basename(flip_path)}) | {post_gap_ms}ms settle | next TTS")

    # Build concat demuxer file list
    concat_path = os.path.join(project.audio_dir, "concat_list.txt")
    files_to_concat = []

    # Short version: the cover is silent (hook merged into P1) but still occupies
    # MIN_PAGE_DURATION of video. Prepend silence so tts_1 aligns with P1's start.
    if project.pages and not project.pages[0].voiceover_text.strip():
        cover_gap_ms = max(0, int((MIN_PAGE_DURATION - PAUSE_AFTER_TTS) * 1000))
        if cover_gap_ms > 0:
            cover_silence = _ensure_silence_file(cover_gap_ms)
            files_to_concat.append(cover_silence)
            print(f"  Audio: +{cover_gap_ms / 1000:.1f}s leading silence (silent cover)")

    for i, page in enumerate(project.pages):
        if i > 0:
            files_to_concat.append(silence_pre)
            files_to_concat.append(flip_path)
            files_to_concat.append(silence_post)
        tts_path = os.path.join(project.audio_dir, f"tts_{page.tts_id}.wav")
        # Silent pages have no TTS audio; skip their slot
        if not page.voiceover_text.strip() or not os.path.exists(tts_path):
            continue
        files_to_concat.append(tts_path)

    # CTA tail page — only when explicitly set (short version historically had a
    # membership CTA tail; 2026-08-18 起移除，cta_text 为空则不追加，长版本绝不带 CTA)
    cta_text = getattr(project, "cta_text", "")
    if cta_text:
        cta_path = os.path.join(project.audio_dir, f"tts_{CTA_TTS_ID}.wav")
        files_to_concat.append(silence_pre)
        files_to_concat.append(flip_path)
        files_to_concat.append(silence_post)
        files_to_concat.append(cta_path)
    if silence_end:
        files_to_concat.append(silence_end)

    # Normalize ALL files before concat
    norm_files = []
    for p in files_to_concat:
        np = ensure_norm_audio(p)
        norm_files.append(np)
    files_to_concat = norm_files

    with open(concat_path, "w", encoding="utf-8") as f:
        for pth in files_to_concat:
            f.write(f"file '{pth}'\n")

    suffix = "_short" if getattr(project, "short_version", False) else ""
    output_path = os.path.join(project.audio_dir, f"voiceover_full{suffix}.wav")
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_path,
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
        output_path
    ], capture_output=True, check=True)
    os.remove(concat_path)

    duration = measure_audio_duration(output_path)
    print(f"  Voiceover assembled: {duration:.1f}s (48kHz WAV, lossless concat) → {output_path}")
    return output_path


# ── CLI Entry ───────────────────────────────────────────────────────────────

def main():
    """CLI entry point for standalone TTS generation."""
    import argparse

    parser = argparse.ArgumentParser(description="TTS Engine — generate voiceover for Global Headlines")
    parser.add_argument("audio_dir", help="Audio directory (e.g. D:/antigravity/Global20260904_long/audio)")
    parser.add_argument("--json", dest="json_path", default=None,
                        help="Path to Globalnews*.json (for page metadata)")
    parser.add_argument("--tts-only", action="store_true", help="Only generate TTS, skip assembly")
    parser.add_argument("--dry-run", action="store_true", help="List pages and TTS ids without generating")
    args = parser.parse_args()

    audio_dir = os.path.abspath(args.audio_dir)
    os.makedirs(audio_dir, exist_ok=True)

    print(f"TTS Engine — audio_dir={audio_dir}")

    # Try to load pages from JSON if provided
    project_pages = []
    if args.json_path and os.path.exists(args.json_path):
        with open(args.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for i, block in enumerate(data.get("news_blocks", [])):
            project_pages.append(PageConfig(
                tts_id=str(i),
                voiceover_text=block.get("content", ""),
                is_flash=False,
            ))
        # Add CTA page
        project_pages.append(PageConfig(tts_id="cta", voiceover_text=CTA_VOICEOVER))

    if args.dry_run:
        print("Dry run — page list:")
        for p in project_pages:
            print(f"  Page {p.tts_id}: '{p.voiceover_text[:40]}...' ({len(p.voiceover_text)} chars)")
        return

    # Create a minimal project object
    project = type('Project', (), {
        'audio_dir': audio_dir,
        'short_version': False,
        'cta_text': CTA_VOICEOVER,
        'pages': project_pages,
    })()

    if not args.tts_only:
        print("\n── Stage 2: Generating TTS ──")
        generate_all_tts(project)
        pad_flash_tts(project)
        generate_cta_tts(project)
        print("\n── Stage 3: Assembling voiceover ──")
        assemble_voiceover(project)
    else:
        print("\n── Stage 2: Generating TTS only ──")
        generate_all_tts(project)
        pad_flash_tts(project)
        generate_cta_tts(project)


if __name__ == "__main__":
    main()
