# HaroClip — Project Context

## What this is

HaroClip automatically generates video clips/highlights, aimed at creator/marketing
campaign use cases. Includes integration with Whop to pull campaign briefs and turn
them into structured data that drives clip generation.

## Tech stack

- **faster-whisper** — speech-to-text
- **Light-ASD** — active speaker detection (vendored from source, no PyPI package)
- **YOLOv8-face** (ultralytics) — face detection
- **ByteTrack** — object/face tracking
- **ffmpeg** — rendering/video processing
- **FastAPI** — backend API layer (webhook ingestion from Whop, job orchestration)
- Frontend: not yet decided, planned for later (dashboard for brief review / clip
  approval) — `frontend/` is currently a placeholder
- Python 3.13, venv (not conda)
- Dependency file: `requirements.txt` (skeleton only — heavy ML deps not yet installed)

## Constraints

- Target hardware: single GPU workstation, **24GB VRAM budget**. Model choices and
  batch sizes need to respect this — no assuming multi-GPU or unlimited VRAM.

## Current status

Project scaffolding complete: folder structure, git repo, `.gitignore`, README,
skeleton `requirements.txt`, `.env.example`. No pipeline code written yet. No heavy
dependencies (torch, ultralytics weights, whisper models) installed yet — deferred
until we start implementation to keep install choices tied to actual GPU/CUDA setup.

## Architecture

Planned across 5 phases (details TBD as implementation proceeds).

## Next steps (not yet started — waiting on direction)

1. Design the prompt for highlight-detection (encode "hook" principles)
2. Build first vertical slice: highlight detection → static render with ffmpeg
3. Extract campaign briefs from Whop into structured JSON

## Working conventions

- Confirm before installing heavy dependencies (model weights, CUDA-specific torch
  builds) — these are large/slow and GPU-specific.
- Don't start implementing a phase without explicit direction on which one.
