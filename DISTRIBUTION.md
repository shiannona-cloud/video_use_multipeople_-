# video-use Distribution Guide

This file is for sharing `video-use` to other AI agents/users with minimal setup friction.

## 1) What to share

Share the full `video-use` skill directory, including:

- `SKILL.md`
- `helpers/`
- `skills/manim-video/` (sub-skill)
- `pyproject.toml`
- `.env.example`
- `README.md`

Do not share local secrets in `.env`.

## 2) Install locations

- Cursor project skill: `.cursor/skills/video-use/`
- Cursor global skill: `~/.cursor/skills/video-use/`
- Claude global skill: `~/.claude/skills/video-use/`

## 3) Runtime requirements

- Python 3.10+
- `ffmpeg` on `PATH` (required)
- `yt-dlp` on `PATH` (optional)
- Python deps:

```bash
pip install -e .
```

Optional animation dependency:

```bash
pip install -e .[animations]
```

## 4) Quick validation after install

1. Ask the agent: `Please use video-use to edit this folder of videos.`
2. Confirm the agent recognizes topic-first flow for dialogue/counseling footage.
3. Confirm outputs are written into `<videos_dir>/edit/` and not into the skill folder.

## 5) Build a clean zip package (Windows PowerShell)

From the `video-use` directory:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-distributable.ps1
```

The script generates:

- `dist/video-use-distributable-<timestamp>.zip`

Excluded by default:

- `.env`
- `video_use.egg-info/`
- `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`
- `.git/`, `.DS_Store`
- `dist/`

## 6) Suggested handoff text

Use this message when sending the zip:

```text
Install this folder as a skill:
- Cursor project: <repo>/.cursor/skills/video-use/
- Cursor global: ~/.cursor/skills/video-use/
- Claude global: ~/.claude/skills/video-use/

Then run in the skill directory:
pip install -e .

Make sure ffmpeg is installed and on PATH.
```
