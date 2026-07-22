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
- Frontend: **React + Vite + TypeScript** (decided on this branch, see `frontend/README.md`)
- Python 3.13, venv (not conda)
- Dependency file: `requirements.txt` (heavy ML deps still not installed; `yt-dlp` +
  `sqlalchemy` added for the ingestion module)

## Constraints

- Compute is **rented on vast.ai**, not run on local hardware (limited local resources).
  Target: RTX 4090/3090, **24GB VRAM budget**, on-demand (not interruptible/spot) instances
  spun up only when a job runs — not kept always-on. Model choices and batch sizes need to
  respect the 24GB ceiling. Full sizing rationale: `docs/hardware-spec.md`.

## Current status

This branch (`ingestion-module`) has a working vertical slice of the video ingestion
module, verified end-to-end:
- Backend (`src/ingestion/`, `src/api/`): `POST /ingestion/jobs` accepts a video URL
  (direct file link or platform link like YouTube/TikTok), validates metadata async via
  ffprobe/yt-dlp (no download at this stage — full download is deferred to the vast.ai
  processing stage), and persists job state in SQLite. `GET /ingestion/jobs/{id}` polls
  for status.
- Frontend (`frontend/`): React/Vite/TS UI to submit a link and watch job status resolve
  (list + detail view), polling the backend every 3s, jobs tracked in `localStorage`.

No heavy ML dependencies (torch, ultralytics weights, whisper models) installed yet —
deferred until those pipeline stages are implemented, to keep install choices tied to
actual GPU/CUDA setup. `main` stays minimal (docs/config only, no code) — see "Git
branching & workflow" below.

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

## Git branching & workflow

- Remote: `https://github.com/AryaSeptiaputra/HaroCLIP.git`
- `main` stays minimal — README, project docs/config (this file, `docs/`,
  `requirements.txt` skeleton, `.env.example`, `.gitignore`), and the empty folder
  structure. No module implementation code is committed directly to `main`.
- Each module's implementation progress is built and pushed on its own branch, named
  after the module (e.g. `ingestion-module` for the video ingestion module — backend
  + frontend). Work in progress lives there until it's ready to fold back into `main`.
- Merge-back policy (PR review vs. direct merge, when to merge) is not decided yet —
  revisit once a module branch is ready to land.
