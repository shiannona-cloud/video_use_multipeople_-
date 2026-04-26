#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path


def main() -> None:
    edit = Path(r"d:\video_user_test\edit")
    seg = json.loads((edit / "topic_segments.json").read_text(encoding="utf-8"))["segments"]
    lines = (edit / "counseling_dialogue_topics.txt").read_text(encoding="utf-8").splitlines()

    pat = re.compile(
        r"^\[(\d+)\] \[(.*?) - (.*?)\] \[说话人: (.*?)\] \[主题: Topic (\d+) \| (.*)\]$"
    )
    by_topic: dict[int, list[tuple[str, str]]] = {int(s["topic_id"]): [] for s in seg}

    i = 0
    while i < len(lines):
        m = pat.match(lines[i].strip())
        if m and i + 1 < len(lines):
            topic_id = int(m.group(5))
            speaker = m.group(4).strip()
            text = lines[i + 1].strip()
            if text and text not in {"，", "。", "？", "!", "！", "、", "嗯", "啊", "哦", "呢", "吧", "是", "好"}:
                by_topic.setdefault(topic_id, []).append((speaker, text))
            i += 3
        else:
            i += 1

    out: list[str] = []
    out.append("话题时间与概述（LLM补充版）")
    out.append("用途：将本文件直接喂给大模型，按“主题线索+代表发言”补全高质量概述。")
    out.append("补充要求：每个话题输出1-2句概述，说明【来访者问题】+【咨询师回应/观点】。")
    out.append("")

    for s in seg:
        topic_id = int(s["topic_id"])
        st = float(s["start"])
        ed = float(s["end"])
        keywords = " / ".join(s.get("keywords", []))
        utterances = int(s.get("utterance_count", 0))

        out.append(f"Topic {topic_id:02d}")
        out.append(f"时间: [{st:.2f} - {ed:.2f}]")
        out.append(f"关键词: {keywords}")
        out.append(f"发言数: {utterances}")
        out.append("代表发言:")

        picks: list[tuple[str, str]] = []
        seen_prefix: set[str] = set()
        for speaker, text in by_topic.get(topic_id, []):
            if len(text) < 6:
                continue
            prefix = text[:30]
            if prefix in seen_prefix:
                continue
            seen_prefix.add(prefix)
            picks.append((speaker, text))
            if len(picks) >= 4:
                break

        if not picks:
            out.append("- （该段需回看原文补充）")
        else:
            for speaker, text in picks:
                out.append(f"- {speaker}: {text}")

        out.append("待补充概述:")
        out.append("- ")
        out.append("")

    out_path = edit / "topic_outline_only.txt"
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"rewrote: {out_path}")


if __name__ == "__main__":
    main()

