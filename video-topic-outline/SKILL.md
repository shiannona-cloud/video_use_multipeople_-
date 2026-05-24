---
name: video-topic-outline
description: Analyze a video transcript (takes_packed.md) and output a structured topic outline in Markdown format (topic_outline_only.md). Use when working with dialogue, counseling, or interview videos and the user asks to generate a topic outline, topic list, topic analysis, or 话题清单. Companion to the video-use skill.
---

# Video Topic Outline

Read `edit/takes_packed.md`, identify all distinct topics, and write `edit/topic_outline_only.md`.

## When to Apply

- User asks for topic analysis, topic list, or 话题清单 from a video transcript
- After `takes_packed.md` has been generated
- Before rough-cut generation or topic selection

## Output Files

Write **both** files to `<videos_dir>/edit/`. Never write inside the skill directory.

| 文件 | 用途 |
|---|---|
| `topic_outline_only.txt` | 兼容 video-use skill Hard Rule 第 17 条，纯文本格式 |
| `topic_outline_only.md` | Markdown 渲染格式，含表格，可读性更好 |

两个文件内容完全一致，仅格式不同。先写 `.txt`，再写 `.md`。

## Analysis Steps

1. **Identify speakers and sessions.** Note if the video has multiple guests/sessions and their rough time boundaries.
2. **Segment by topic.** A new topic begins when the subject clearly shifts. Dialogue within one topic (question → counselor response → follow-up) stays in the same topic. Keep Q&A pairs together — never split a question from its answer.
3. **Count phrases per topic** from `takes_packed.md` line count within the range.
4. **For counseling/dialogue content:** flag any counselor commentary on social news as high-priority (default keep).
5. **Apply safety handles** to every topic range: expand each boundary by +2s head and +2s tail (clamp to source bounds).

## Output Format

Produce `topic_outline_only.md` using the template below. Field order is fixed.

```markdown
# 话题清单 · <filename>（约 XX 分钟）

生成时间：YYYY-MM-DD

---

## 连线分段说明

| 段落 | 来访者/嘉宾 | 时间段 | 时长 |
|---|---|---|---|
| 第一段连线 | 姓名（身份） | Xs – Xs | 约 XX 分钟 |
| 过渡闲聊 | — | Xs – Xs | 约 X 分钟 |
| 第二段连线 | 姓名（身份） | Xs – Xs | 约 XX 分钟 |

---

## 话题总览

### 第一段连线 · 姓名（起止时间）

| # | 话题 | 时间段 |
|---|---|---|
| 01 | 话题名称 | Xs–Xs |

---

## 话题详情

### 第一段连线 · 姓名

---

#### Topic 01 · 话题名称

- **时间：** Xs – Xs
- **关键词：** 关键词1、关键词2、关键词3
- **发言数：** 约 N 条短语
- **话题概述：** 1–2 句，覆盖来访者问题 + 咨询师回应/观点。

**代表发言：**

> 来访者："……"

> 咨询师："……"

---

## 汇总

| 项目 | 内容 |
|---|---|
| 总话题数 | N 个（含 N 个过渡段） |
| 第一段连线 | Topic 01–0N |
| 社会评论段 | Topic NN（默认保留） |
| 视频总时长 | 约 XX 分钟 |
```

## Field Rules

| 字段 | 要求 |
|---|---|
| 话题概述 | 1–2 句，具体可读，不要关键词堆砌；双边视角（来访者困境 + 咨询师回应） |
| 代表发言 | 2–4 条，用引用块，标注说话人 |
| 关键词 | 3–6 个，中文为主，英文术语保留原文 |
| 时间 | 整数秒，精确到帧不必要 |
| 过渡段 | 标注为 `—`，写一行备注说明内容，可删减 |

## Social News Commentary Rule

If counselor proactively comments on social news / gender issues / cultural phenomena (not prompted by the guest), mark the topic:

```
- **优先级：** 🔴 高（咨询师社会评论，默认保留）
```

## After Writing

Report to the user:
1. Total topics found
2. Any high-priority social commentary topics
3. Ask: "请告诉我你想保留哪些话题，我再生成粗剪片段。"

Do not generate rough cuts or EDL until the user selects topics.

## Reference

See `edit/topic_outline_only.md` and `edit/topic_outline_only.txt` in this project for complete worked examples.
