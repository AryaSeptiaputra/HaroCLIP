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
- Frontend: **React + Vite + TypeScript** (decided independently on this branch and on
  `ingestion-module`; each module branch currently scaffolds its own frontend project —
  see "Git branching & workflow")
- Python 3.13, venv (not conda)
- Dependency file: `requirements.txt` (heavy ML deps still not installed; `sqlalchemy` +
  `python-multipart` added for the campaign brief module)

## Constraints

- Compute is **rented on vast.ai**, not run on local hardware (limited local resources).
  Target: RTX 4090/3090, **24GB VRAM budget**, on-demand (not interruptible/spot) instances
  spun up only when a job runs — not kept always-on. Model choices and batch sizes need to
  respect the 24GB ceiling. Full sizing rationale: `docs/hardware-spec.md`.

## Current status

This branch (`campaign-module`) has a working vertical slice of the campaign brief
module, verified end-to-end:
- Backend (`src/campaign/`, `src/api/`): `POST /campaign/briefs` accepts a brief as
  either pasted free text or an uploaded PDF/DOCX/TXT file (mutually exclusive, one
  required, plus a required `title`), storing it as-is — no parsing/extraction into
  structured data yet (deferred to a future module, since real briefs have highly
  variable formats). `GET /campaign/briefs` lists all briefs, `GET /campaign/briefs/{id}`
  fetches one, `GET /campaign/briefs/{id}/file` downloads an uploaded file.
- Frontend (`frontend/`): React/Vite/TS UI — submit form (text/file mode toggle), brief
  list, detail view (inline text or file metadata + download link).

Branched from `main`, independent of `ingestion-module` — does not include ingestion
code (per the branching convention below), so this branch's `src/api/main.py` and
`frontend/` were scaffolded fresh rather than extending ingestion's.

No heavy ML dependencies (torch, ultralytics weights, whisper models) installed — a
stray `pip install -r requirements.txt` earlier pulled in torch/ultralytics/faster-whisper
transitively because the original skeleton had those uncommented; they've been removed
from the venv and commented out in `requirements.txt` (matching how `torch`/`torchvision`
were already handled) until a module actually needs them with a real GPU/CUDA setup.
**Lesson: check what's already uncommented in `requirements.txt` before running a blanket
`pip install -r requirements.txt`** — don't assume it only installs what you just added.

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
