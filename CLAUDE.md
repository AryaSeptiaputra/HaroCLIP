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

`main` carries four verified vertical slices, merged in from their module branches
(`highlights-module` merged 2026-07-25):

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
- **Highlight detection** (`src/highlights/`, `src/transcription/`, `src/rendering/`):
  given a `ready` `ProcessingJob`, `python -m src.highlights.run --job-id
  <ingestion_job_id> [--force]` transcribes `audio.wav` via faster-whisper
  (large-v3/int8), feeds the timestamped transcript to a **local LLM
  (Qwen2.5-7B-Instruct, 4-bit via `transformers`+`bitsandbytes`)** prompted with
  hand-designed "hook" principles (`src/highlights/prompt.py`) to get 5-10 ranked
  candidate segments as strict JSON, then renders each as a static clip via ffmpeg.
  Tracked in `highlight_jobs`/`highlight_clips`. Local-LLM choice was an explicit user
  decision (not an external API).
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

This branch (`reframe-module`) adds face detection + tracking + dynamic vertical-crop
rendering: for every `HighlightClip`, `python -m src.reframe.run --job-id
<ingestion_job_id> [--force]` samples the clip at a fixed 5fps (cost scales with sampled
frame count, not full framerate — `docs/hardware-spec.md`), runs YOLOv8-face
(`src/detection/face_detector.py`) on each sampled frame, feeds detections through
ByteTrack (`src/tracking/face_tracker.py`, via `supervision`) to get consistent face
tracks, picks a **primary speaker via a heuristic** (`src/reframe/speaker_selection.py`
— largest total on-screen area, tie-break by longest-persisting track), builds a
smoothed/interpolated horizontal crop path (`src/reframe/crop_path.py` — pure
numpy math, no I/O) for a fixed-height 9:16 vertical strip that pans to follow the
speaker (never crops vertically), then renders it via OpenCV (per-frame crop+resize to
1080×1920) with a final ffmpeg pass to remux the original audio
(`src/reframe/renderer.py`). Tracked in `reframe_jobs` (`src/reframe/`), keyed by
`highlight_clip_id` — one reframe job per clip, unlike other modules' per-ingestion-job
granularity (the CLI still takes `--job-id <ingestion_job_id>` for UX consistency and
loops over that job's clips internally).

**Heuristic active-speaker selection is a deliberate placeholder, not real Light-ASD.**
Researched during planning (fetched the actual upstream `Junhua-Liao/Light-ASD` repo):
its own demo does its own internal face detection/tracking rather than accepting
external tracks, so real integration means vendoring `ASD.py`/`model/Model.py`/
`model/Encoder.py`/`model/Classifier.py` and hand-implementing its exact preprocessing
(112×112 grayscale face crops; MFCC audio at a strict 4-audio-frames:1-video-frame
alignment; `module.`-prefix checkpoint loading) — none of which could be exercised
locally (no GPU, no weights), unlike the rest of this module. User chose to ship the
swappable heuristic now (`select_primary_track()` is the single isolated seam meant to
be replaced later) and defer real Light-ASD to a follow-up. **YOLOv8-face weights are a
manual prerequisite** (not pip-installable): `YOLOV8_FACE_WEIGHTS_PATH` env var,
default `data/models/yolov8n-face-lindevs.pt` — source: community repo
`lindevs/yolov8-face` (WIDERFace-trained, MIT-licensed), must be downloaded onto the
vast.ai instance manually, same category as the `ffmpeg` binary being a documented
prerequisite rather than a `requirements.txt` entry.

**Verification caveat (highlights-module, still current):** local dev has no CUDA GPU,
so faster-whisper transcription and the Qwen2.5-7B highlight-detection LLM were only
verified structurally (clean import, prompt rendering, `parse_candidates` against fake
responses, mocked service flow) — actual transcription accuracy and highlight quality
are still unverified pending a real vast.ai run.

**Verification caveat (reframe-module):** this module has a much better local story —
`speaker_selection.select_primary_track` and `crop_path.build_crop_path` are pure
functions, fully unit-tested locally (interpolation, smoothing, clamping, empty-input
fallback all verified with hand-built fake data). `renderer.render_reframed_clip` was
run **for real** (not mocked) against a synthetic test video — confirmed exact
1080×1920 output, correct duration, audio remuxed correctly. The full
`service.run_reframe` status flow / idempotency / `--force` behavior was verified with
`FaceDetector`/`FaceTracker` stubbed out (those two remain unverified for real — need
actual YOLOv8-face weights + a real GPU — everything downstream of them is verified).

## Architecture

Planned across 5 phases (details TBD as implementation proceeds).

## Next steps (not yet started — waiting on direction)

1. Run the full pipeline end-to-end on a real vast.ai GPU instance with the manually-
   downloaded YOLOv8-face weights in place: transcription accuracy, LLM highlight
   quality, YOLOv8-face/ByteTrack behavior on real footage, actual crop quality, and
   actual VRAM usage vs `docs/hardware-spec.md` estimates — nothing beyond the
   mocked/stubbed local checks has run for real yet.
2. Swap `src/reframe/speaker_selection.py`'s heuristic for real vendored Light-ASD
   (`Junhua-Liao/Light-ASD`) once there's a real GPU to develop/test the audio-visual
   preprocessing against — `select_primary_track()` is the intended seam.

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
