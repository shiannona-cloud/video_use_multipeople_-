"""Transcribe a video with FunASR and emit video-use compatible JSON.

Extracts mono 16kHz audio via ffmpeg, runs local FunASR inference, then
normalizes output into a `words` timeline compatible with the existing
pack/render/timeline helpers.

Cached: if the output file already exists, transcription is skipped.

Usage:
    python helpers/transcribe.py <video_path>
    python helpers/transcribe.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe.py <video_path> --language zh
    python helpers/transcribe.py <video_path> --model paraformer-zh
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    from funasr import AutoModel
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    AutoModel = None
    _FUNASR_IMPORT_ERROR = exc
else:
    _FUNASR_IMPORT_ERROR = None


DEFAULT_MODEL = os.environ.get("FUNASR_MODEL", "paraformer-zh")
DEFAULT_VAD_MODEL = os.environ.get("FUNASR_VAD_MODEL", "fsmn-vad")
DEFAULT_PUNC_MODEL = os.environ.get("FUNASR_PUNC_MODEL", "ct-punc")


def _ensure_funasr() -> None:
    if AutoModel is not None:
        return
    msg = (
        "FunASR is not installed. Run:\n"
        "  python -m pip install funasr\n"
        f"Import error: {_FUNASR_IMPORT_ERROR}"
    )
    sys.exit(msg)


def extract_audio(video_path: Path, dest: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _iter_result_blocks(payload: object) -> list[dict]:
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    return []


def _text_to_tokens(text: str) -> list[str]:
    # Chinese commonly has no spaces; fall back to per-character tokens.
    text = text.strip()
    if not text:
        return []
    if " " in text:
        return [t for t in text.split() if t]
    return [ch for ch in text if ch.strip()]


def _char_level_tokens(text: str) -> list[str]:
    """Fallback tokenizer: strip whitespace, keep per-character granularity."""
    return [ch for ch in text if not ch.isspace()]


def _append_word(words: list[dict], start: float, end: float, text: str, speaker_id: str | None = None) -> None:
    words.append({
        "type": "word",
        "text": text,
        "start": round(float(start), 3),
        "end": round(float(end), 3),
        "speaker_id": speaker_id,
    })


def _append_spacing(words: list[dict], prev_end: float | None, current_start: float) -> None:
    if prev_end is None:
        return
    if current_start <= prev_end:
        return
    words.append({
        "type": "spacing",
        "text": "",
        "start": round(float(prev_end), 3),
        "end": round(float(current_start), 3),
        "speaker_id": None,
    })


def normalize_funasr_to_words(payload: object) -> list[dict]:
    words: list[dict] = []
    prev_end: float | None = None

    for block in _iter_result_blocks(payload):
        # Path A: sentence_info with per sentence timing + text
        sentence_info = block.get("sentence_info")
        if isinstance(sentence_info, list) and sentence_info:
            for sent in sentence_info:
                if not isinstance(sent, dict):
                    continue
                st = sent.get("start")
                et = sent.get("end")
                txt = str(sent.get("text", "")).strip()
                if st is None or et is None or not txt:
                    continue
                start_s = float(st) / 1000.0 if float(st) > 50 else float(st)
                end_s = float(et) / 1000.0 if float(et) > 50 else float(et)
                tokens = _text_to_tokens(txt)
                if not tokens:
                    continue
                dur = max(0.01, end_s - start_s)
                step = dur / len(tokens)
                _append_spacing(words, prev_end, start_s)
                for i, token in enumerate(tokens):
                    ws = start_s + i * step
                    we = end_s if i == len(tokens) - 1 else (start_s + (i + 1) * step)
                    _append_word(words, ws, we, token, speaker_id=sent.get("spk"))
                prev_end = end_s
            continue

        # Path B: timestamp + text
        ts = block.get("timestamp")
        text = str(block.get("text", "")).strip()
        if isinstance(ts, list) and ts and text:
            token_spans: list[tuple[float, float]] = []
            for item in ts:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    continue
                st, et = item[0], item[1]
                try:
                    st_f = float(st)
                    et_f = float(et)
                except (TypeError, ValueError):
                    continue
                # Most FunASR timestamp values are milliseconds.
                if st_f > 50 or et_f > 50:
                    st_f /= 1000.0
                    et_f /= 1000.0
                token_spans.append((st_f, et_f))
            tokens = _text_to_tokens(text)
            # Some FunASR outputs contain sparse spaces that collapse long
            # Chinese passages into a handful of tokens. If timestamp spans
            # are far denser than token count, fallback to char-level tokens.
            if token_spans and len(tokens) * 2 < len(token_spans):
                tokens = _char_level_tokens(text)
            if token_spans:
                start_s = token_spans[0][0]
                _append_spacing(words, prev_end, start_s)
                for idx, (st_f, et_f) in enumerate(token_spans):
                    token = tokens[idx] if idx < len(tokens) else ""
                    if not token:
                        continue
                    _append_word(words, st_f, et_f, token)
                prev_end = token_spans[-1][1]
            continue

    words.sort(key=lambda w: (w.get("start", 0.0), w.get("end", 0.0)))
    return words


def call_funasr(
    audio_path: Path,
    model_name: str,
    vad_model: str | None,
    punc_model: str | None,
    language: str | None = None,
    num_speakers: int | None = None,  # kept for CLI compatibility
) -> dict:
    _ensure_funasr()
    kwargs: dict[str, object] = {"model": model_name}
    if vad_model:
        kwargs["vad_model"] = vad_model
    if punc_model:
        kwargs["punc_model"] = punc_model
    if language:
        kwargs["lang"] = language
    if num_speakers:
        # Placeholder: most open FunASR paths do not provide stable diarization.
        kwargs["spk_model"] = None

    model = AutoModel(**kwargs)
    raw = model.generate(input=str(audio_path))
    words = normalize_funasr_to_words(raw)
    return {
        "provider": "funasr",
        "model": model_name,
        "language": language,
        "raw_result": raw,
        "words": words,
    }


def transcribe_one(
    video: Path,
    edit_dir: Path,
    model_name: str = DEFAULT_MODEL,
    vad_model: str | None = DEFAULT_VAD_MODEL,
    punc_model: str | None = DEFAULT_PUNC_MODEL,
    language: str | None = None,
    num_speakers: int | None = None,
    verbose: bool = True,
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    Cached: returns existing path immediately if the transcript already exists.
    """
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcripts_dir / f"{video.stem}.json"

    if out_path.exists():
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    if verbose:
        print(f"  extracting audio from {video.name}", flush=True)

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{video.stem}.wav"
        extract_audio(video, audio)
        size_mb = audio.stat().st_size / (1024 * 1024)
        if verbose:
            print(f"  transcribing {video.stem}.wav ({size_mb:.1f} MB) with FunASR", flush=True)
        payload = call_funasr(
            audio_path=audio,
            model_name=model_name,
            vad_model=vad_model,
            punc_model=punc_model,
            language=language,
            num_speakers=num_speakers,
        )

    out_path.write_text(json.dumps(payload, indent=2))
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        if isinstance(payload, dict) and "words" in payload:
            print(f"    words: {len(payload['words'])}")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video with FunASR")
    ap.add_argument("video", type=Path, help="Path to video file")
    ap.add_argument(
        "--edit-dir",
        type=Path,
        default=None,
        help="Edit output directory (default: <video_parent>/edit)",
    )
    ap.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"FunASR model name (default: {DEFAULT_MODEL})",
    )
    ap.add_argument(
        "--vad-model",
        type=str,
        default=DEFAULT_VAD_MODEL,
        help=f"FunASR VAD model (default: {DEFAULT_VAD_MODEL})",
    )
    ap.add_argument(
        "--punc-model",
        type=str,
        default=DEFAULT_PUNC_MODEL,
        help=f"FunASR punctuation model (default: {DEFAULT_PUNC_MODEL})",
    )
    ap.add_argument(
        "--language",
        type=str,
        default=None,
        help="Optional language hint (e.g., zh, en). Omit to use model default.",
    )
    ap.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Reserved for compatibility; diarization depends on selected FunASR pipeline.",
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        model_name=args.model,
        vad_model=args.vad_model,
        punc_model=args.punc_model,
        language=args.language,
        num_speakers=args.num_speakers,
    )


if __name__ == "__main__":
    main()
