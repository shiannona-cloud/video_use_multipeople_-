"""Chunked transcription to eliminate timestamp drift on long audio.

FunASR's paraformer model accumulates timestamp errors on long audio files
(progressive drift of ~3-5s per minute of speech). This script splits audio
at silence boundaries, transcribes each chunk independently, and merges
results with correct absolute time offsets.

Additionally fixes the punctuation-timestamp alignment bug: FunASR's ct-punc
model inserts punctuation into the text AFTER ASR, but timestamps only cover
speech characters. The normalize step must skip punctuation when mapping
timestamps to text.

Usage:
    python helpers/transcribe_chunked.py <video_path>
    python helpers/transcribe_chunked.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe_chunked.py <video_path> --chunk-duration 60
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    from funasr import AutoModel
except ImportError as exc:
    AutoModel = None
    _FUNASR_IMPORT_ERROR = exc
else:
    _FUNASR_IMPORT_ERROR = None

DEFAULT_MODEL = os.environ.get("FUNASR_MODEL", "paraformer-zh")
DEFAULT_VAD_MODEL = os.environ.get("FUNASR_VAD_MODEL", "fsmn-vad")
DEFAULT_PUNC_MODEL = os.environ.get("FUNASR_PUNC_MODEL", "ct-punc")

CJK_PUNCT = set("，。？！、；：""''（）【】…—～·《》〈〉「」『』")
LATIN_PUNCT = set(",.?!;:\"'()[]{}—–-…~/<>")
ALL_PUNCT = CJK_PUNCT | LATIN_PUNCT


def _ensure_funasr() -> None:
    if AutoModel is not None:
        return
    sys.exit(
        "FunASR is not installed. Run:\n"
        "  python -m pip install funasr\n"
        f"Import error: {_FUNASR_IMPORT_ERROR}"
    )


def extract_full_audio(video_path: Path, dest: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def detect_silences(audio_path: Path, min_duration: float = 0.8, threshold: int = -35) -> list[tuple[float, float]]:
    """Detect silence periods using ffmpeg silencedetect."""
    cmd = [
        "ffmpeg", "-i", str(audio_path), "-af",
        f"silencedetect=noise={threshold}dB:d={min_duration}",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    stderr = result.stderr

    silences = []
    starts = re.findall(r"silence_start: ([\d.]+)", stderr)
    ends = re.findall(r"silence_end: ([\d.]+)", stderr)

    for s, e in zip(starts, ends):
        silences.append((float(s), float(e)))
    if len(starts) > len(ends) and starts:
        silences.append((float(starts[-1]), float("inf")))

    return silences


def find_split_points(
    silences: list[tuple[float, float]],
    audio_duration: float,
    target_chunk: float = 60.0,
    min_chunk: float = 20.0,
) -> list[float]:
    """Find optimal split points at silence midpoints, targeting chunk_duration."""
    if not silences:
        points = []
        t = target_chunk
        while t < audio_duration - min_chunk:
            points.append(t)
            t += target_chunk
        return points

    split_points = []
    last_split = 0.0

    for s_start, s_end in silences:
        elapsed = s_start - last_split
        if elapsed >= target_chunk * 0.7:
            mid = (s_start + min(s_end, s_start + 2.0)) / 2.0
            split_points.append(mid)
            last_split = mid
        elif elapsed >= target_chunk * 1.5:
            mid = (s_start + min(s_end, s_start + 2.0)) / 2.0
            split_points.append(mid)
            last_split = mid

    return split_points


def extract_chunk(audio_path: Path, start: float, end: float, dest: Path) -> None:
    duration = end - start
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(audio_path),
        "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def get_audio_duration(audio_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def is_punct(ch: str) -> bool:
    return ch in ALL_PUNCT


def normalize_chunk_result(
    payload: object,
    time_offset: float,
) -> tuple[list[dict], str]:
    """Normalize a single chunk's funASR result with correct punctuation handling.

    Returns (words_list, raw_text).
    The key fix: timestamps map 1:1 to speech characters (excluding punctuation).
    Punctuation is merged into the preceding word's text.
    """
    words: list[dict] = []

    blocks = payload if isinstance(payload, list) else [payload]
    full_text = ""

    for block in blocks:
        if not isinstance(block, dict):
            continue

        text = str(block.get("text", "")).strip()
        timestamps = block.get("timestamp", [])

        if not text or not timestamps:
            continue

        full_text += text

        token_spans: list[tuple[float, float]] = []
        for item in timestamps:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            st, et = float(item[0]), float(item[1])
            if st > 50 or et > 50:
                st /= 1000.0
                et /= 1000.0
            token_spans.append((st + time_offset, et + time_offset))

        if not token_spans:
            continue

        speech_chars = []
        for ch in text:
            if ch.isspace():
                continue
            if not is_punct(ch):
                speech_chars.append(ch)

        n_ts = len(token_spans)
        n_speech = len(speech_chars)

        if n_speech == 0:
            continue

        if abs(n_ts - n_speech) > n_ts * 0.1:
            all_chars = [ch for ch in text if not ch.isspace()]
            for idx, (st, et) in enumerate(token_spans):
                ch = all_chars[idx] if idx < len(all_chars) else ""
                if ch:
                    words.append({
                        "type": "word",
                        "text": ch,
                        "start": round(st, 3),
                        "end": round(et, 3),
                        "speaker_id": None,
                    })
            continue

        ts_idx = 0
        text_no_space = [ch for ch in text if not ch.isspace()]

        for ch in text_no_space:
            if is_punct(ch):
                if words:
                    words[-1]["text"] += ch
                    words[-1]["end"] = max(
                        words[-1]["end"],
                        token_spans[min(ts_idx, n_ts - 1)][0] if ts_idx < n_ts else words[-1]["end"]
                    )
                continue

            if ts_idx < n_ts:
                st, et = token_spans[ts_idx]
                words.append({
                    "type": "word",
                    "text": ch,
                    "start": round(st, 3),
                    "end": round(et, 3),
                    "speaker_id": None,
                })
                ts_idx += 1
            else:
                if words:
                    words[-1]["text"] += ch

    return words, full_text


def transcribe_chunked(
    video: Path,
    edit_dir: Path,
    model_name: str = DEFAULT_MODEL,
    vad_model: str | None = DEFAULT_VAD_MODEL,
    punc_model: str | None = DEFAULT_PUNC_MODEL,
    chunk_duration: float = 60.0,
    verbose: bool = True,
) -> Path:
    """Transcribe video using chunked approach for accurate timestamps."""
    _ensure_funasr()

    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcripts_dir / f"{video.stem}.json"

    if out_path.exists():
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    t0 = time.time()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        audio_path = tmp / f"{video.stem}.wav"

        if verbose:
            print(f"  extracting audio from {video.name}", flush=True)
        extract_full_audio(video, audio_path)
        audio_duration = get_audio_duration(audio_path)

        if verbose:
            size_mb = audio_path.stat().st_size / (1024 * 1024)
            print(f"  audio: {size_mb:.1f} MB, duration: {audio_duration:.1f}s", flush=True)
            print(f"  detecting silence boundaries...", flush=True)

        silences = detect_silences(audio_path)
        split_points = find_split_points(silences, audio_duration, target_chunk=chunk_duration)

        boundaries = [0.0] + split_points + [audio_duration]
        chunks = [(boundaries[i], boundaries[i + 1]) for i in range(len(boundaries) - 1)]

        if verbose:
            print(f"  split into {len(chunks)} chunks (target {chunk_duration}s)", flush=True)

        kwargs: dict = {"model": model_name}
        if vad_model:
            kwargs["vad_model"] = vad_model
        if punc_model:
            kwargs["punc_model"] = punc_model

        model = AutoModel(**kwargs)

        all_words: list[dict] = []
        all_raw_results = []
        prev_end: float | None = None

        for i, (chunk_start, chunk_end) in enumerate(chunks):
            chunk_path = tmp / f"chunk_{i:04d}.wav"
            extract_chunk(audio_path, chunk_start, chunk_end, chunk_path)

            if verbose:
                print(
                    f"  [{i+1}/{len(chunks)}] transcribing {chunk_start:.1f}s - {chunk_end:.1f}s "
                    f"({chunk_end - chunk_start:.1f}s)",
                    flush=True,
                )

            raw = model.generate(input=str(chunk_path), batch_size_s=300)
            all_raw_results.append({"chunk_start": chunk_start, "chunk_end": chunk_end, "result": raw})

            chunk_words, _ = normalize_chunk_result(raw, time_offset=chunk_start)

            if not chunk_words:
                continue

            if prev_end is not None and chunk_words[0]["start"] > prev_end:
                all_words.append({
                    "type": "spacing",
                    "text": "",
                    "start": round(prev_end, 3),
                    "end": round(chunk_words[0]["start"], 3),
                    "speaker_id": None,
                })

            all_words.extend(chunk_words)
            prev_end = chunk_words[-1]["end"]

            chunk_path.unlink()

    all_words.sort(key=lambda w: (w["start"], w["end"]))

    payload = {
        "provider": "funasr_chunked",
        "model": model_name,
        "language": None,
        "chunk_duration": chunk_duration,
        "num_chunks": len(chunks),
        "audio_duration": audio_duration,
        "raw_results": all_raw_results,
        "words": all_words,
    }

    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        word_count = sum(1 for w in all_words if w["type"] == "word")
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        print(f"    words: {word_count}")
        if all_words:
            first_t = next((w["start"] for w in all_words if w["type"] == "word"), 0)
            last_t = next((w["end"] for w in reversed(all_words) if w["type"] == "word"), 0)
            print(f"    time range: {first_t:.1f}s - {last_t:.1f}s")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Chunked transcription with accurate timestamps")
    ap.add_argument("video", type=Path, help="Path to video file")
    ap.add_argument("--edit-dir", type=Path, default=None)
    ap.add_argument("--model", type=str, default=DEFAULT_MODEL)
    ap.add_argument("--vad-model", type=str, default=DEFAULT_VAD_MODEL)
    ap.add_argument("--punc-model", type=str, default=DEFAULT_PUNC_MODEL)
    ap.add_argument(
        "--chunk-duration", type=float, default=60.0,
        help="Target chunk duration in seconds (default: 60)",
    )
    ap.add_argument("--force", action="store_true", help="Overwrite existing transcript")
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()
    out_path = edit_dir / "transcripts" / f"{video.stem}.json"

    if args.force and out_path.exists():
        out_path.unlink()

    transcribe_chunked(
        video=video,
        edit_dir=edit_dir,
        model_name=args.model,
        vad_model=args.vad_model,
        punc_model=args.punc_model,
        chunk_duration=args.chunk_duration,
    )


if __name__ == "__main__":
    main()
