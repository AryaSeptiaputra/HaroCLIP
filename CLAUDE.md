# HaroClip — Project Context

## What this is

HaroClip automatically generates video clips/highlights, aimed at creator/marketing
campaign use cases. Includes integration with Whop to pull campaign briefs and turn
them into structured data that drives clip generation.

## Tech stack

- **faster-whisper** — speech-to-text
- **Qwen2.5-7B-Instruct** (via `transformers`/`bitsandbytes`, 4-bit) — local LLM for
  highlight-detection ("hook" scoring from transcript, not an external API)
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

`main` carries three verified vertical slices, merged in from their module branches:

- **Ingestion** (`src/ingestion/`, `src/api/routers/ingestion.py`): `POST
  /ingestion/jobs` validates a submitted video link (direct file or platform link) via
  ffprobe/yt-dlp — no download at this stage, that's deferred to the processing stage —
  and persists status in SQLite (`pending`/`validating`/`ready`/`failed`). `GET
  /ingestion/jobs/{id}` polls for status.
- **Campaign briefs** (`src/campaign/`, `src/api/routers/campaign.py`): `POST
  /campaign/briefs` accepts a brief as pasted text or an uploaded PDF/DOCX/TXT file
  (mutually exclusive, one required, plus a required `title`), storing it as-is — no
  structured extraction yet. Status: likely to be removed later (per user, 2026-07-25)
  — don't build new features that assume campaign briefs stick around (e.g. the
  highlight-detection LLM prompt deliberately ignores brief content for this reason).
- **Download & pre-processing** (`src/processing/`): given a `ready` `IngestionJob`,
  `python -m src.processing.run --job-id <id> [--force]` downloads the full video
  (re-resolving fresh via yt-dlp for platform links, or streaming a direct URL via
  `httpx`) to `data/videos/<ingestion_job_id>/source.<ext>`, then extracts its audio
  track to `audio.wav` (16kHz mono PCM) via ffmpeg. Tracked in `processing_jobs`,
  referencing `ingestion_jobs.id` by plain string (no real FK). CLI-only, idempotent
  without `--force`.
- Frontend (`frontend/`): single React/Vite/TS app with a top-level tab switch between
  "Ingestion" and "Campaign Briefs" views. **UI work is paused** (per user, 2026-07-25)
  until all backend modules are done — later modules (highlights and beyond) are
  CLI-only for now, no frontend changes expected until that's revisited.

Heavy ML dependencies (`faster-whisper`, `transformers`/`accelerate`/`bitsandbytes`,
`ultralytics`, `supervision`, `torch`, `torchvision`) remain commented out in
`requirements.txt` and not installed — deferred until run on a real vast.ai GPU
instance. (Lesson learned on `campaign-module`: a blanket `pip install -r
requirements.txt` once pulled these in transitively because the original skeleton had
them uncommented — always check what's already uncommented before running a blanket
install.)

This branch (`highlights-module`) adds the first highlight-detection vertical slice:
given a `ready` `ProcessingJob`, `python -m src.highlights.run --job-id <ingestion_job_id>
[--force]` transcribes `audio.wav` via faster-whisper (`src/transcription/`,
large-v3/int8), feeds the timestamped transcript to a **local LLM (Qwen2.5-7B-Instruct,
4-bit via `transformers`+`bitsandbytes`)** prompted with hand-designed "hook" principles
(`src/highlights/prompt.py` — curiosity gap, surprising claim, emotional peak, concrete
insight, controversial opinion; clips ~15-60s, natural sentence boundaries,
self-contained) to get 5-10 ranked candidate segments as strict JSON
(`src/highlights/llm.py` parses + validates timestamps/duration, drops malformed
entries rather than failing the whole job), then renders each as a static clip via
ffmpeg (`src/rendering/clipper.py` — plain temporal cut, `-ss`/`-t` as *input* options
so re-encoding stays frame-accurate without the `-ss`+`-to` absolute-timeline gotcha;
no dynamic cropping/face-tracking yet, that's a later phase). Tracked in
`highlight_jobs`/`highlight_clips` (`src/highlights/`), same loose string-reference
convention as `processing_jobs`. **Local LLM choice was an explicit user decision**
(not an external API) — VRAM budget assumes sequential load/free per stage (whisper
freed before the LLM loads), see `docs/hardware-spec.md`.

**Verification caveat:** local dev has no CUDA GPU, so only the non-GPU parts were
verified this session — package imports cleanly with heavy deps absent (lazy imports,
same pattern as `processing.downloader`), prompt template rendering, `parse_candidates`
against hand-written fake LLM responses (valid/malformed/out-of-range), `render_clip`
against a real synthetic test video, and the full `service.run_highlight_detection`
status flow / idempotency / `--force` behavior with `transcribe`/`generate_candidates`
mocked out. **Actual transcription accuracy and actual LLM highlight quality are
unverified** — needs a real vast.ai GPU instance run before trusting the output.

## Architecture

Planned across 5 phases (details TBD as implementation proceeds).

## Next steps (not yet started — waiting on direction)

1. Run the highlights pipeline end-to-end on a real vast.ai GPU instance (transcription
   accuracy, LLM highlight quality, actual VRAM usage vs `docs/hardware-spec.md`
   estimates) — nothing beyond the mocked/stubbed local checks has run for real yet.
2. Face detection / active speaker detection / tracking (`src/detection/`,
   `src/tracking/`) for dynamic vertical-crop rendering — current rendering is a static
   temporal cut only.

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
