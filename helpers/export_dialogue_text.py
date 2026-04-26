#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def sec_to_hhmmss(sec: float) -> str:
    ms = int(round(sec * 1000))
    h = ms // 3_600_000
    ms %= 3_600_000
    m = ms // 60_000
    ms %= 60_000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def pick_topic(topics: list[dict], t0: float, t1: float) -> dict | None:
    mid = (t0 + t1) / 2.0
    for topic in topics:
        if topic["start"] <= mid <= topic["end"]:
            return topic
    for topic in topics:
        if t0 <= topic["end"] and t1 >= topic["start"]:
            return topic
    return None


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Export timestamped counseling dialogue with topic labels."
    )
    ap.add_argument("--edit-dir", required=True, help="Path to <videos_dir>/edit")
    ap.add_argument(
        "--pause-threshold",
        type=float,
        default=0.6,
        help="Gap threshold (seconds) to split utterances",
    )
    args = ap.parse_args()

    edit_dir = Path(args.edit_dir).resolve()
    transcripts_dir = edit_dir / "transcripts"
    transcript_files = sorted(transcripts_dir.glob("*.json"))
    if not transcript_files:
        raise SystemExit(f"no transcript found in {transcripts_dir}")

    transcript_path = transcript_files[0]
    topic_path = edit_dir / "topic_segments.json"
    if not topic_path.exists():
        raise SystemExit(f"topic file not found: {topic_path}")

    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    topics = json.loads(topic_path.read_text(encoding="utf-8")).get("segments", [])
    words = transcript.get("words", [])
    if not words:
        raise SystemExit(f"empty words in transcript: {transcript_path}")

    utterances: list[dict] = []
    buf: list[str] = []
    u_start = float(words[0]["start"])
    u_end = float(words[0]["end"])

    for idx, w in enumerate(words):
        text = str(w.get("text", ""))
        start = float(w.get("start", u_end))
        end = float(w.get("end", start))
        if idx == 0:
            buf.append(text)
            continue

        prev_end = float(words[idx - 1].get("end", start))
        if start - prev_end >= args.pause_threshold:
            utt_text = "".join(buf).strip()
            if utt_text:
                utterances.append({"start": u_start, "end": u_end, "text": utt_text})
            buf = [text]
            u_start = start
            u_end = end
        else:
            buf.append(text)
            u_end = end

    tail_text = "".join(buf).strip()
    if tail_text:
        utterances.append({"start": u_start, "end": u_end, "text": tail_text})

    out_path = edit_dir / "counseling_dialogue_topics.txt"
    lines: list[str] = []
    lines.append("咨询对话文本（含时间戳/说话人/主题）")
    lines.append(f"来源转写: {transcript_path.name}")
    lines.append(
        "说话人说明: 原始转写未提供稳定 speaker_id，本文件按停顿切分并使用“咨询者/咨询师”轮替推定。"
    )
    lines.append("")

    for i, u in enumerate(utterances, start=1):
        role = "咨询者" if i % 2 == 1 else "咨询师"
        topic = pick_topic(topics, u["start"], u["end"])
        if topic:
            topic_label = (
                f"Topic {int(topic['topic_id']):02d} | {topic['summary']}"
            )
        else:
            topic_label = "Topic 未匹配"
        lines.append(
            f"[{i:04d}] [{sec_to_hhmmss(u['start'])} - {sec_to_hhmmss(u['end'])}] "
            f"[说话人: {role}] [主题: {topic_label}]"
        )
        lines.append(u["text"])
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote: {out_path}")
    print(f"utterances: {len(utterances)}")


if __name__ == "__main__":
    main()

