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

`main` carries four verified vertical slices, merged in from their module branches
(`highlights-module`, `reframe-module`, and `remove-campaign-module` all merged
2026-07-25):

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
- **Reframe** (`src/reframe/`, `src/detection/`, `src/tracking/`): for every
  `HighlightClip`, `python -m src.reframe.run --job-id <ingestion_job_id> [--force]`
  samples the clip at a fixed 5fps (cost scales with sampled frame count, not full
  framerate — `docs/hardware-spec.md`), runs YOLOv8-face
  (`src/detection/face_detector.py`) on each sampled frame, feeds detections through
  ByteTrack (`src/tracking/face_tracker.py`, via `supervision`) to get consistent face
  tracks, picks a **primary speaker** (`src/reframe/speaker_selection.py` —
  `select_primary_track_with_asd()`, see below), builds a smoothed/interpolated
  horizontal crop path (`src/reframe/crop_path.py` — pure numpy math, no I/O) for a
  fixed-height 9:16 vertical strip that pans to follow the speaker (never crops
  vertically), then renders it via OpenCV (per-frame crop+resize to 1080×1920) with a
  final ffmpeg pass to remux the original audio (`src/reframe/renderer.py`). Tracked in
  `reframe_jobs`, keyed by `highlight_clip_id` — one reframe job per clip, unlike other
  modules' per-ingestion-job granularity (the CLI still takes `--job-id
  <ingestion_job_id>` for UX consistency and loops over that job's clips internally).
  **YOLOv8-face weights are a manual prerequisite** (not pip-installable):
  `YOLOV8_FACE_WEIGHTS_PATH` env var, default `data/models/yolov8n-face-lindevs.pt` —
  source: community repo `lindevs/yolov8-face` (WIDERFace-trained, MIT-licensed), must
  be downloaded onto the vast.ai instance manually (or baked into the Docker image —
  see `Dockerfile`), same category as the `ffmpeg` binary being a documented
  prerequisite rather than a `requirements.txt` entry.
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

**Real Light-ASD is vendored** (`src/detection/light_asd/`, MIT-licensed, upstream
`github.com/Junhua-Liao/Light-ASD`, attribution + deviations documented in
`src/detection/light_asd/NOTICE.md`) — fetched verbatim from the real upstream source
(not paraphrased), including `Columbia_test.py`'s exact `evaluate_network`/
`crop_video` algorithm, so the integration in `src/reframe/asd_scoring.py`
(`LightASDScorer`) is a faithful port: smoothed/padded square face crop per frame
(112×112 grayscale, median-filtered center/size), MFCC audio (13 coefs,
4-audio-frames:1-video-frame alignment), multi-duration ensemble scoring, raw-logit
score thresholded at 0. One correctness fix beyond upstream: `reframe`'s face sampling
is 5fps, but Light-ASD's temporal convs expect dense ~25fps input, so `asd_scoring.py`
resamples the interpolated crop sequence onto a synthetic 25fps timeline (by time, not
native frame index) before scoring — feeding it raw 5fps samples would have silently
produced garbage scores. `asd.py` (vendored, trimmed) drops upstream's training-only
`train_network`/CSV-`evaluate_network` (avoids a needless `pandas` dependency); model
construction and weight loading are otherwise byte-identical to upstream except
`loadParameters` gains a `map_location` for more portable checkpoint loading, and
`__init__` uses `torch.device("cuda" if torch.cuda.is_available() else "cpu")` instead
of upstream's hardcoded `.cuda()` (CPU fallback needed for local verification; no
behavior change on the real CUDA deployment target).

`speaker_selection.select_primary_track_with_asd(video_path, tracks, source_fps)` tries
real Light-ASD scoring per candidate face track (mean raw-logit score, highest wins),
and **falls back to a pure heuristic** (`select_primary_track()` — largest on-screen
area, tie-break by duration) on any failure: missing deps (`ImportError`), missing
weights, or a scoring exception. This means the pipeline behaves the same whether or
not `torch`/`python_speech_features` + weights are actually available — zero risk of
the whole job failing just because ASD couldn't run.

Heavy ML dependencies (`faster-whisper`, `transformers`/`accelerate`/`bitsandbytes`,
`ultralytics`, `supervision`, `torch`, `torchvision`) remain commented out in
`requirements.txt` and not installed — deferred until run on a real vast.ai GPU
instance. (Lesson learned early on: a blanket `pip install -r requirements.txt` once
pulled these in transitively because the original skeleton had them uncommented —
always check what's already uncommented before running a blanket install.)

**Verification caveat (highlights-module):** local dev has no CUDA GPU, so only the
non-GPU parts of highlight detection were verified on this path — package imports
cleanly with heavy deps absent (lazy imports, same pattern as
`processing.downloader`), prompt template rendering, `parse_candidates` against
hand-written fake LLM responses (valid/malformed/out-of-range), `render_clip` against a
real synthetic test video, and the full `service.run_highlight_detection` status flow /
idempotency / `--force` behavior with `transcribe`/`generate_candidates` mocked out.
**Actual transcription accuracy and actual LLM highlight quality are unverified** —
needs a real vast.ai GPU instance run before trusting the output.

**Verification status (reframe-module — real local ML verification, not just
mocked):** the project venv (`C:\Project\HaroCLIP\venv`) now has real (small-model)
versions of the full heavy ML stack installed ad hoc — `torch` (CPU-only build, see
tooling note below), `faster-whisper`, `transformers`, `accelerate`, `ultralytics`,
`supervision`, `python_speech_features`. **Not reflected in `requirements.txt`**, which
still stays commented per the existing convention (it documents the production/vast.ai
install list, e.g. the CUDA torch build, not this dev venv's ad hoc local state).

Every stage now has **real, non-mocked local verification** using small model
substitutes for the production-size models (all downloaded/cached in the local HF
cache, RTX 3050 4GB present but unused since torch is the CPU build — see tooling note):
- **Transcription**: `transcribe()` ran for real against the synthetic test video's
  audio with `WHISPER_MODEL_SIZE=tiny`, `WHISPER_DEVICE=cpu` — confirmed model load +
  inference + correctly-typed `TranscriptSegment` results (content is garbage, as
  expected — the test audio is a sine tone, not speech).
- **Highlight LLM**: `generate_candidates()` ran for real against a fake transcript
  with `HIGHLIGHT_LLM_MODEL=Qwen/Qwen2.5-0.5B-Instruct`, `HIGHLIGHT_LLM_4BIT=0`,
  `HIGHLIGHT_LLM_DEVICE_MAP=cpu` — produced syntactically valid JSON (confirms the
  prompt→generation→parsing wiring is correct) but with all-zero timestamps, which
  `parse_candidates()` correctly rejected. This is the small model's capability limit
  (0.5B isn't strong enough to reason about numeric timestamps tied to text), not a
  code bug — the validator did exactly its job. `run_highlight_detection()` run
  end-to-end for real reaches `FAILED`/`no valid highlight candidates survived
  validation` for this same reason, confirming the full chain (transcribe → generate →
  parse) executes correctly up through this small model's actual limitation.
- **Detection**: `FaceDetector.detect()` ran for real (`yolov8n-face-lindevs.pt`,
  downloaded to `data/models/`) against a synthetic test frame — 0 boxes (no real face
  present), confirms model load + inference + return-type correctness.
- **Tracking**: `FaceTracker.update()` ran for real against empty detections — confirms
  the `sv.Detections`/`ByteTrack` wiring doesn't crash on the zero-detections path.
  (`supervision.ByteTrack` shows a deprecation warning — removed in v0.30, currently on
  0.29.1 — noted as a future maintenance item, not urgent.)
- **Light-ASD**: `LightASDScorer()` construction ran for real — confirms
  `loadParameters` actually loads the vendored ~4MB checkpoint (1,021,120 params) into
  the real model. `score_track()` itself still can't be meaningfully exercised without
  a real face+speech clip (none exists locally) — the one remaining verification gap.
- **Full pipeline regression**: `service.run_reframe()` ran **fully to `READY`** with
  real `FaceDetector`+`FaceTracker` (no mocks) against the synthetic test clip,
  producing a real output file — the first time this project's dynamic-crop path has
  run end-to-end with real detection/tracking calls, anywhere.

**CPU-vs-CUDA device flexibility added this session**, specifically to make this local
verification possible without matching CUDA driver versions: `light_asd/asd.py`
(`torch.device("cuda" if torch.cuda.is_available() else "cpu")` instead of upstream's
hardcoded `.cuda()`), `asd_scoring.py`'s ensemble scoring (`.to(self._model.device)`
instead of `.cuda()`), and every `torch.cuda.empty_cache()` cleanup call across
`transcriber.py`/`llm.py`/`face_detector.py` now guarded with
`if torch.cuda.is_available()`. New env vars for this same purpose, all defaulting to
production behavior when unset: `WHISPER_MODEL_SIZE` (default `large-v3`),
`WHISPER_DEVICE` (default `cuda`), `HIGHLIGHT_LLM_MODEL` (default
`Qwen/Qwen2.5-7B-Instruct`), `HIGHLIGHT_LLM_4BIT` (default on), `HIGHLIGHT_LLM_DEVICE_MAP`
(default `cuda`). None of this changes behavior on the real vast.ai deployment target
(CUDA always available there) — it only makes local CPU verification possible.

**Still unverified even after this pass:** actual production-scale model quality —
whisper large-v3 transcription accuracy, Qwen2.5-7B highlight judgment, YOLOv8-face/
ByteTrack/Light-ASD *accuracy* against real footage (vs. just "doesn't crash" against a
synthetic video with zero real faces) — needs either a real face+speech test clip
locally or a real vast.ai GPU run.

**Tooling notes (discovered this session, cost real debugging time — read before
running anything ML-related locally):**
- This machine has a stray global Python 3.10 install
  (`C:\Users\Arya\AppData\Local\Programs\Python\Python310\python.exe`) with unrelated
  leftover ML packages that Git Bash's `python`/`python3` on `PATH` resolve to by
  default. **Always invoke `C:/Project/HaroCLIP/venv/Scripts/python.exe` explicitly**,
  not bare `python`/`python3`.
- **Large file downloads (torch wheels, etc.) reliably fail when run via a backgrounded
  shell command** (both `pip install` and direct `curl`) — they get killed almost
  immediately after the transfer starts, regardless of file size (tested: 2.5GB CUDA
  wheel, 206MB and 122MB CPU wheels all failed the same way). **A plain foreground shell
  call with a generous timeout works fine** (confirmed steady ~1MB/s throughput, no
  issue) — this is specific to how backgrounded downloads are handled, not a real
  network or sandbox block. If a large download is needed and might exceed the ~10min
  foreground cap, prefer a smaller model variant over fighting the backgrounding issue.
- Installing full CUDA torch (~2.5GB) wasn't achievable within a single foreground
  call's time budget at observed throughput (~42min needed vs. ~10min cap) — used the
  CPU-only build (~200MB, ~4min) instead. GPU went effectively unused for this session's
  verification as a result (confirmed via `nvidia-smi`: ~1% utilization throughout).
- `huggingface_hub`'s Rust-based `hf-xet` fast-download accelerator threw a low-level
  `MemoryError`/Rust panic when downloading a model — worked around with
  `HF_HUB_DISABLE_XET=1` (forces the plain Python downloader) or by pre-downloading via
  `hf download <model>` in the user's own terminal first, then running with
  `HF_HUB_OFFLINE=1` so the script never touches the network.

## Architecture

Planned across 5 phases (details TBD as implementation proceeds).

## Next steps (not yet started — waiting on direction)

1. Get a real short face+speech test clip (e.g. a webcam recording) to close the one
   remaining local-verification gap: actual YOLOv8-face/ByteTrack/Light-ASD behavior
   against real content, still only checked for "doesn't crash" against a synthetic
   video with zero real faces so far.
2. Run the full pipeline end-to-end on a real vast.ai GPU instance with production-size
   models (whisper large-v3, Qwen2.5-7B) and the manually-downloaded (or Docker-baked)
   YOLOv8-face weights in place: transcription accuracy, LLM highlight quality at full
   model size, real-footage detection/tracking/ASD accuracy, actual VRAM usage vs
   `docs/hardware-spec.md` estimates. Wiring for all four stages is now verified
   locally (small-model substitutes, real inference, not mocks) — this is about
   production-scale model quality and real GPU resource usage specifically.

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
