"""Topic-aware segmentation for counseling-style dialogue videos.

This helper reads transcript JSON files from <edit>/transcripts and creates
topic segments that prefer complete dialogue turns over aggressive cuts.

Output:
  - <edit>/topic_segments.json
  - <edit>/topic_segments.md

Usage:
  python helpers/topic_segment.py --edit-dir <videos_dir>/edit
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import jieba


PUNCT_END = set("。！？!?;；.")
STOPWORDS = {
    "的", "了", "和", "是", "我", "你", "他", "她", "它", "在", "就", "都", "而", "及", "与", "着",
    "啊", "嗯", "呢", "吧", "吗", "这个", "那个", "我们", "你们", "他们", "然后", "所以", "就是",
}


@dataclass
class Utterance:
    source: str
    start: float
    end: float
    speaker_id: str | None
    text: str
    tokens: list[str]


def _normalize_text(text: str) -> str:
    text = re.sub(r"\s+", "", text or "")
    return text.strip()


def _tokenize(text: str) -> list[str]:
    words = []
    for tok in jieba.lcut(text, cut_all=False):
        tok = tok.strip()
        if not tok or tok in STOPWORDS:
            continue
        if re.fullmatch(r"[0-9\W_]+", tok):
            continue
        words.append(tok)
    return words


def _group_words_into_utterances(source: str, words: list[dict], silence_threshold: float) -> list[Utterance]:
    utterances: list[Utterance] = []
    current_words: list[dict] = []
    current_speaker: str | None = None
    current_start: float | None = None
    prev_end: float | None = None

    def flush() -> None:
        nonlocal current_words, current_speaker, current_start
        if not current_words or current_start is None:
            current_words = []
            current_speaker = None
            current_start = None
            return
        end_time = float(current_words[-1].get("end", current_start))
        pieces = []
        for w in current_words:
            raw = str(w.get("text", "")).strip()
            if not raw:
                continue
            if w.get("type") == "audio_event" and not raw.startswith("("):
                raw = f"({raw})"
            pieces.append(raw)
        text = _normalize_text("".join(pieces))
        if text:
            utterances.append(
                Utterance(
                    source=source,
                    start=float(current_start),
                    end=end_time,
                    speaker_id=current_speaker,
                    text=text,
                    tokens=_tokenize(text),
                )
            )
        current_words = []
        current_speaker = None
        current_start = None

    for w in words:
        w_type = w.get("type", "word")
        if w_type not in {"word", "audio_event"}:
            continue
        ws = w.get("start")
        we = w.get("end")
        if ws is None:
            continue
        ws = float(ws)
        we = float(we if we is not None else ws)
        spk = w.get("speaker_id")

        gap = (ws - prev_end) if prev_end is not None else 0.0
        speaker_change = current_speaker is not None and spk is not None and spk != current_speaker
        if gap >= silence_threshold or speaker_change:
            flush()

        if current_start is None:
            current_start = ws
            current_speaker = spk
        current_words.append(w)
        prev_end = we

    flush()
    return utterances


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a[k] * b[k] for k in set(a) & set(b))
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _lexical_shift(utterances: list[Utterance], idx: int, window_size: int) -> float:
    left = utterances[max(0, idx - window_size):idx]
    right = utterances[idx:min(len(utterances), idx + window_size)]
    left_counts = Counter(tok for u in left for tok in u.tokens)
    right_counts = Counter(tok for u in right for tok in u.tokens)
    sim = _cosine(left_counts, right_counts)
    return 1.0 - sim


def _is_dialogue_checkpoint(segment_utts: list[Utterance], candidate_prev: Utterance) -> bool:
    # Prefer cutting after a complete conversational exchange:
    # 1) both sides have spoken in this segment (if speaker labels exist)
    # 2) previous utterance ends with a sentence-like punctuation
    speakers = {u.speaker_id for u in segment_utts if u.speaker_id is not None}
    has_turns = len(speakers) >= 2 or not speakers
    ends_cleanly = bool(candidate_prev.text) and candidate_prev.text[-1] in PUNCT_END
    return has_turns and ends_cleanly


def build_segments(
    utterances: list[Utterance],
    min_segment_sec: float,
    max_segment_sec: float,
    shift_threshold: float,
    window_size: int,
) -> list[dict]:
    if not utterances:
        return []

    segments: list[dict] = []
    seg_start_idx = 0

    for i in range(1, len(utterances)):
        seg_start = utterances[seg_start_idx].start
        seg_end = utterances[i - 1].end
        seg_duration = seg_end - seg_start

        if seg_duration < min_segment_sec:
            continue

        prev_u = utterances[i - 1]
        seg_utts = utterances[seg_start_idx:i]
        shift = _lexical_shift(utterances, i, window_size=window_size)
        checkpoint = _is_dialogue_checkpoint(seg_utts, prev_u)
        force_by_length = seg_duration >= max_segment_sec

        if checkpoint and (shift >= shift_threshold or force_by_length):
            topic_text = " ".join(u.text for u in seg_utts[-8:])
            keywords = [k for k, _ in Counter(_tokenize(topic_text)).most_common(6)]
            segments.append({
                "topic_id": len(segments) + 1,
                "source": seg_utts[0].source,
                "start": round(seg_start, 3),
                "end": round(seg_end, 3),
                "duration_s": round(seg_duration, 3),
                "summary": f"围绕“{'/'.join(keywords[:3]) or '当前问题'}”展开的一段咨询对话",
                "keywords": keywords,
                "utterance_count": len(seg_utts),
            })
            seg_start_idx = i

    # Tail segment
    if seg_start_idx < len(utterances):
        tail = utterances[seg_start_idx:]
        seg_start = tail[0].start
        seg_end = tail[-1].end
        topic_text = " ".join(u.text for u in tail[-8:])
        keywords = [k for k, _ in Counter(_tokenize(topic_text)).most_common(6)]
        segments.append({
            "topic_id": len(segments) + 1,
            "source": tail[0].source,
            "start": round(seg_start, 3),
            "end": round(seg_end, 3),
            "duration_s": round(seg_end - seg_start, 3),
            "summary": f"围绕“{'/'.join(keywords[:3]) or '当前问题'}”展开的一段咨询对话",
            "keywords": keywords,
            "utterance_count": len(tail),
        })

    return segments


def _render_markdown(segments: list[dict]) -> str:
    lines = ["# Topic Segments", ""]
    for s in segments:
        lines.append(f"## Topic {s['topic_id']:02d}  [{s['start']:.2f}-{s['end']:.2f}] ({s['duration_s']:.1f}s)")
        lines.append(f"- summary: {s['summary']}")
        lines.append(f"- keywords: {', '.join(s['keywords']) if s['keywords'] else '(none)'}")
        lines.append(f"- utterances: {s['utterance_count']}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _collect_utterances(transcripts_dir: Path, silence_threshold: float) -> list[Utterance]:
    utterances: list[Utterance] = []
    for path in sorted(transcripts_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        words = data.get("words", [])
        utterances.extend(_group_words_into_utterances(path.stem, words, silence_threshold))
    return utterances


def main() -> None:
    ap = argparse.ArgumentParser(description="Topic-aware dialogue segmentation for transcripts.")
    ap.add_argument("--edit-dir", type=Path, required=True, help="Edit directory containing transcripts/")
    ap.add_argument("--silence-threshold", type=float, default=0.8, help="Gap to split utterances (seconds)")
    ap.add_argument("--min-segment-sec", type=float, default=120.0, help="Minimum segment length")
    ap.add_argument("--max-segment-sec", type=float, default=900.0, help="Maximum segment length")
    ap.add_argument("--shift-threshold", type=float, default=0.62, help="Lexical shift threshold for topic split")
    ap.add_argument("--window-size", type=int, default=6, help="Utterance window size for lexical shift")
    args = ap.parse_args()

    edit_dir = args.edit_dir.resolve()
    transcripts_dir = edit_dir / "transcripts"
    if not transcripts_dir.is_dir():
        raise SystemExit(f"no transcripts directory at {transcripts_dir}")

    utterances = _collect_utterances(transcripts_dir, args.silence_threshold)
    if not utterances:
        raise SystemExit("no usable utterances found in transcripts")

    segments = build_segments(
        utterances=utterances,
        min_segment_sec=args.min_segment_sec,
        max_segment_sec=args.max_segment_sec,
        shift_threshold=args.shift_threshold,
        window_size=args.window_size,
    )

    out_json = edit_dir / "topic_segments.json"
    out_md = edit_dir / "topic_segments.md"
    out_json.write_text(json.dumps({"segments": segments}, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(_render_markdown(segments), encoding="utf-8")

    print(f"utterances: {len(utterances)}")
    print(f"segments: {len(segments)}")
    print(f"wrote: {out_json}")
    print(f"wrote: {out_md}")


if __name__ == "__main__":
    main()
