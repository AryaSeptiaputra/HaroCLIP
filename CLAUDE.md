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
- Frontend: **React + Vite + TypeScript**, single app with a top-level tab switch
  between modules — see `frontend/README.md`
- Python 3.13, venv (not conda)
- Dependency file: `requirements.txt` (heavy ML deps still not installed; `yt-dlp`,
  `sqlalchemy`, `python-multipart` added for the ingestion/campaign modules)

## Constraints

- Compute is **rented on vast.ai**, not run on local hardware (limited local resources).
  Target: RTX 4090/3090, **24GB VRAM budget**, on-demand (not interruptible/spot) instances
  spun up only when a job runs — not kept always-on. Model choices and batch sizes need to
  respect the 24GB ceiling. Full sizing rationale: `docs/hardware-spec.md`.

## Current status

`main` now carries two verified vertical slices, merged in from their module branches:

- **Ingestion** (`src/ingestion/`, `src/api/routers/ingestion.py`): `POST
  /ingestion/jobs` validates a submitted video link (direct file or platform link) via
  ffprobe/yt-dlp — no download at this stage, that's deferred to the processing stage —
  and persists status in SQLite (`pending`/`validating`/`ready`/`failed`). `GET
  /ingestion/jobs/{id}` polls for status.
- **Campaign briefs** (`src/campaign/`, `src/api/routers/campaign.py`): `POST
  /campaign/briefs` accepts a brief as pasted text or an uploaded PDF/DOCX/TXT file
  (mutually exclusive, one required, plus a required `title`), storing it as-is — no
  structured extraction yet (deferred to a future module, since real briefs vary too
  much in format). List/get/file-download endpoints included.
- Frontend (`frontend/`): single React/Vite/TS app with a top-level tab switch between
  "Ingestion" and "Campaign Briefs" views; each retains its own component tree and
  state, ported over unchanged from its module branch.

Heavy ML dependencies (`faster-whisper`, `ultralytics`, `supervision`, `torch`,
`torchvision`) remain commented out in `requirements.txt` and not installed — deferred
until a module actually needs them with a real GPU/CUDA setup. (Lesson learned on
`campaign-module`: a blanket `pip install -r requirements.txt` once pulled these in
transitively because the original skeleton had them uncommented — always check what's
already uncommented before running a blanket install.)

This branch (`processing-module`) adds the download & pre-processing stage, verified
end-to-end: given a `ready` `IngestionJob`, `python -m src.processing.run --job-id <id>
[--force]` downloads the full video (re-resolving fresh via yt-dlp for platform links —
`download=True` this time, unlike ingestion's probe-only `download=False` — or streaming
a direct URL via `httpx`) to `data/videos/<ingestion_job_id>/source.<ext>`, then extracts
its audio track to `audio.wav` (16kHz mono PCM, faster-whisper's native input format) via
an `ffmpeg` subprocess. Tracked in its own `processing_jobs` table (`src/processing/`),
referencing `ingestion_jobs.id` by plain string (no real FK). CLI-only by design — this
runs once per vast.ai GPU instance boot for a specific job, not as an always-on HTTP
service like ingestion/campaign. Idempotent without `--force`.

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
- Each module's implementation progress is built and pushed on its own branch, named
  after the module (e.g. `ingestion-module`, `campaign-module`, `processing-module`).
- **Merge-back policy (decided):** a module branch merges into `main` via `git merge
  --no-ff <branch>` once its vertical slice is built and verified end-to-end — no PR
  review step at this project's current single-developer stage (revisit if that
  changes). Merging isn't strictly tied to completion order — it's triggered by a real
  dependency need (e.g. `ingestion-module` and `campaign-module` merged into `main`
  together specifically because `processing-module` needs direct access to
  `IngestionJob`'s model/schema, which isolated per-branch development couldn't provide).
- A module branch may stay unmerged for a while if nothing later depends on it yet —
  that's fine, merging is need-driven, not automatic on completion.
