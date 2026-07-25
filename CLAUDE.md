# HaroClip — Project Context

## What this is

HaroClip automatically generates video clips/highlights from a source video, aimed at
creator/marketing use cases: submit a video link, it downloads the video, transcribes
it, uses a local LLM to find "hook"-worthy highlight segments, and renders each as a
dynamic vertical-crop clip that follows the speaker.

## Tech stack

- **faster-whisper** — speech-to-text
- **Qwen2.5-7B-Instruct** (via `transformers`/`bitsandbytes`, 4-bit) — local LLM for
  highlight-detection ("hook" scoring from transcript, not an external API)
- **Light-ASD** — active speaker detection (vendored from source, no PyPI package)
- **YOLOv8-face** (ultralytics) — face detection
- **ByteTrack** — object/face tracking
- **ffmpeg** — rendering/video processing
- **FastAPI** — backend API layer (job orchestration)
- Frontend: **React + Vite + TypeScript** — see `frontend/README.md`
- Python 3.13, venv (not conda)
- Dependency file: `requirements.txt` (heavy ML deps still not installed; `yt-dlp`,
  `sqlalchemy` added for the ingestion module)

## Constraints

- Compute is **rented on vast.ai**, not run on local hardware (limited local resources).
  Target: RTX 4090/3090, **24GB VRAM budget**, on-demand (not interruptible/spot) instances
  spun up only when a job runs — not kept always-on. Model choices and batch sizes need to
  respect the 24GB ceiling. Full sizing rationale: `docs/hardware-spec.md`.

## Current status

`main` carries three verified vertical slices, merged in from their module branches:

- **Ingestion** (`src/ingestion/`, `src/api/routers/ingestion.py`): `POST
  /ingestion/jobs` validates a submitted video link (direct file or platform link) via
  ffprobe/yt-dlp — no download at this stage, that's deferred to the processing stage —
  and persists status in SQLite (`pending`/`validating`/`ready`/`failed`). `GET
  /ingestion/jobs/{id}` polls for status.
- **Download & pre-processing** (`src/processing/`): given a `ready` `IngestionJob`,
  `python -m src.processing.run --job-id <id> [--force]` downloads the full video
  (re-resolving fresh via yt-dlp for platform links, or streaming a direct URL via
  `httpx`) to `data/videos/<ingestion_job_id>/source.<ext>`, then extracts its audio
  track to `audio.wav` (16kHz mono PCM) via ffmpeg. Tracked in `processing_jobs`,
  referencing `ingestion_jobs.id` by plain string (no real FK). CLI-only, idempotent
  without `--force`.
- **Highlight detection** (`src/highlights/`, `src/transcription/`, `src/rendering/`):
  given a `ready` `ProcessingJob`, `python -m src.highlights.run --job-id
  <ingestion_job_id> [--force]` transcribes `audio.wav` via faster-whisper
  (large-v3/int8), feeds the timestamped transcript to a **local LLM
  (Qwen2.5-7B-Instruct, 4-bit via `transformers`+`bitsandbytes`)** prompted with
  hand-designed "hook" principles (`src/highlights/prompt.py` — curiosity gap,
  surprising claim, emotional peak, concrete insight, controversial opinion; clips
  ~15-60s, natural sentence boundaries, self-contained) to get 5-10 ranked candidate
  segments as strict JSON (`src/highlights/llm.py` parses + validates
  timestamps/duration, drops malformed entries rather than failing the whole job), then
  renders each as a static clip via ffmpeg (`src/rendering/clipper.py` — plain temporal
  cut, `-ss`/`-t` as *input* options so re-encoding stays frame-accurate without the
  `-ss`+`-to` absolute-timeline gotcha). Tracked in `highlight_jobs`/`highlight_clips`,
  same loose string-reference convention as `processing_jobs`. **Local LLM choice was
  an explicit user decision** (not an external API) — VRAM budget assumes sequential
  load/free per stage (whisper freed before the LLM loads), see
  `docs/hardware-spec.md`.
- Frontend (`frontend/`): React/Vite/TS app, currently just the ingestion view (no more
  tab shell — that was for switching to the now-removed campaign briefs module).
  **UI work is paused** (per user, 2026-07-25) until all backend modules are done —
  later modules (highlights and beyond) are CLI-only for now, no frontend changes
  expected until that's revisited.

**Campaign briefs module removed (2026-07-25, per user direction).** Previously
accepted a brief as pasted text or an uploaded PDF/DOCX/TXT file and stored it as-is,
with structured extraction deferred to a future module that never got built. Decided
not to pursue this direction — removed from `main` (`src/campaign/`,
`src/api/routers/campaign.py`, the campaign frontend view/components/hook, and the
`requests`/`python-multipart` dependencies that existed only for it) rather than left
half-built. The `campaign-module` branch itself is kept on GitHub as archived history,
not deleted. If Whop integration or campaign-brief-driven highlight targeting comes
back later, treat it as a fresh module, not a resurrection of this code.

Heavy ML dependencies (`faster-whisper`, `transformers`/`accelerate`/`bitsandbytes`,
`ultralytics`, `supervision`, `torch`, `torchvision`) remain commented out in
`requirements.txt` and not installed — deferred until run on a real vast.ai GPU
instance. (Lesson learned early on: a blanket `pip install -r requirements.txt` once
pulled these in transitively because the original skeleton had them uncommented —
always check what's already uncommented before running a blanket install.)

**Verification caveat:** local dev has no CUDA GPU, so only the non-GPU parts of
highlight detection were verified — package imports cleanly with heavy deps absent
(lazy imports, same pattern as `processing.downloader`), prompt template rendering,
`parse_candidates` against hand-written fake LLM responses (valid/malformed/
out-of-range), `render_clip` against a real synthetic test video, and the full
`service.run_highlight_detection` status flow / idempotency / `--force` behavior with
`transcribe`/`generate_candidates` mocked out. **Actual transcription accuracy and
actual LLM highlight quality are unverified** on `main` — needs a real vast.ai GPU
instance run before trusting the output. (Separately, on the still-unmerged
`reframe-module` branch — dynamic-crop rendering, face detection/tracking, real
Light-ASD — real non-mocked local verification with small model substitutes has since
been done; see that branch's history if picking this back up.)

## Architecture

Planned across 5 phases (details TBD as implementation proceeds).

## Next steps (not yet started — waiting on direction)

1. Decide whether/when to merge `reframe-module` into `main` — it already builds face
   detection/tracking/dynamic-crop rendering and real Light-ASD (item 2 below), with
   real local verification done, but isn't merged yet.
2. ~~Face detection / active speaker detection / tracking for dynamic vertical-crop
   rendering~~ — done on `reframe-module` (unmerged).
3. Run the full pipeline end-to-end on a real vast.ai GPU instance (transcription
   accuracy, LLM highlight quality at full model size, real-footage detection/
   tracking/ASD accuracy, actual VRAM usage vs `docs/hardware-spec.md` estimates) —
   nothing beyond local checks (mocked on `main`, real-small-model on `reframe-module`)
   has run at production scale yet.

## Working conventions

- Confirm before installing heavy dependencies (model weights, CUDA-specific torch
  builds) — these are large/slow and GPU-specific.
- Don't start implementing a phase without explicit direction on which one.
- UI work is paused until all backend modules are done (per user, 2026-07-25) — don't
  add frontend views/components for new modules unless explicitly asked to resume.

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
