# HaroClip — Project Context

## What this is

HaroClip automatically generates video clips/highlights from a source video, aimed at
creator/marketing use cases: submit a video link, it downloads the video, transcribes
it, uses the Claude API to find "hook"-worthy highlight segments, renders each as a
dynamic vertical-crop clip that follows the speaker, and burns in auto-generated
word-burst captions as the final step.

## Tech stack

- **faster-whisper** — speech-to-text (float16, GPU)
- **Claude API** (Anthropic, `anthropic` SDK) — highlight-detection ("hook" scoring
  from the full transcript in one call, no local model/GPU involved). Was a local LLM
  (Qwen2.5-7B-Instruct) until 2026-07-25 — see "Current status" for why that changed.
- **Light-ASD** — active speaker detection (vendored from source, no PyPI package)
- **YOLOv8-face** (ultralytics, **xlarge** variant, `imgsz=1280`) — face detection
- **ByteTrack** — object/face tracking
- **ffmpeg** — rendering/video processing, plus caption burn-in via its `subtitles`
  filter (libass) — no separate subtitle-rendering library
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
2026-07-25). A fifth, **Captioning**, was added 2026-07-26 on the still-unmerged
`vast-ai-e2e-prep` branch (alongside that branch's Claude API switch — see "Next
steps" for merge status):

- **Ingestion** (`src/ingestion/`, `src/api/routers/ingestion.py`): `POST
  /ingestion/jobs` validates a submitted video link (direct file or platform link) via
  ffprobe/yt-dlp — no download at this stage, that's deferred to the processing stage —
  and persists status in SQLite (`pending`/`validating`/`ready`/`failed`). `GET
  /ingestion/jobs/{id}` polls for status. `IngestionJob.campaign_context` (added
  2026-07-29, optional) holds a freeform descriptive prompt that steers highlight
  selection toward a campaign's intent — see the "Highlight detection" bullet below
  and "Campaign context returns as a prompt" further down for the full story.
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
  (large-v3/**float16**), feeds the timestamped transcript to the **Claude API**
  (`src/highlights/llm.py`, `anthropic` SDK — `ANTHROPIC_API_KEY` required) prompted
  with hand-designed "hook" principles (`src/highlights/prompt.py` — curiosity gap,
  surprising claim, emotional peak, concrete insight, controversial opinion; clips
  **30s-3min**, spread across the *entire* video rather than clustered in one section,
  natural sentence boundaries, self-contained), **optionally layered with
  `IngestionJob.campaign_context`** (`build_system_prompt()` — an additional filter
  appended to the base system prompt, not a replacement: still has to be a genuine
  hook first, campaign relevance breaks ties rather than overriding the hook bar) to
  get 5-10 ranked candidate segments as
  strict JSON (`parse_candidates` validates timestamps/duration, drops malformed
  entries rather than failing the whole job), then renders each as a static clip via
  ffmpeg (`src/rendering/clipper.py` — plain temporal cut, `-ss`/`-t` as *input* options
  so re-encoding stays frame-accurate without the `-ss`+`-to` absolute-timeline gotcha).
  Tracked in `highlight_jobs`/`highlight_clips`, same loose string-reference convention
  as `processing_jobs`.

  **Claude's raw response is cached to disk** (`HighlightJob.llm_response_path`,
  written the moment the API call succeeds, same sibling-file pattern as
  `transcript_path`) **so a resume after a later-stage failure (parsing/rendering)
  reuses it instead of re-calling the paid API** — added 2026-07-27 after two
  live-run incidents already burned real credit re-diagnosing/re-running.
  `--force` always bypasses the cache for a deliberate fresh call. Existing
  `data/haroclip.db` files created before this change need a one-time manual
  `ALTER TABLE highlight_jobs ADD COLUMN llm_response_path VARCHAR;` — this project
  has no migration system (`init_db()` is a bare `Base.metadata.create_all()`,
  which never adds columns to an existing table), see `VAST_GUIDE.md`.

  **LLM choice switched from local (Qwen2.5-7B) to the Claude API on 2026-07-26**,
  after the first real vast.ai run (57-min video) exposed a real bug: all 10 returned
  candidates clustered in the first 89 seconds, in mechanical back-to-back 5s chunks —
  not genuine hook selection. Root cause: the full formatted transcript (57k chars,
  2062 timestamped segments) came to roughly **31k tokens, right at Qwen2.5-7B's
  32,768-token context limit**, triggering "lost in the middle" degradation rather
  than an outright truncation error. Cost/architecture analysis before switching:
  Claude's much larger context window fits a 2-3hr video's transcript in one call with
  no chunking needed; per-video API cost estimate (~$0.20-0.31, Sonnet-tier, unverified
  current pricing — check anthropic.com/pricing) came in at or below the self-hosted
  GPU-rental estimate once the local model's one-time checkpoint-download bottleneck
  (up to ~75min for a 72B model) was factored in; and it removes the highlight stage's
  GPU/VRAM requirement entirely. **User kept the same RTX 3090 24GB GPU tier anyway**
  and redirected the VRAM the local LLM no longer needs toward quality upgrades on the
  stages that still run locally: whisper `int8`→`float16`, YOLOv8-face `nano`→
  `medium`. `HIGHLIGHT_LLM_MODEL` env var (default `claude-sonnet-5`) still exists for
  overriding which Claude model is used; `HIGHLIGHT_LLM_4BIT`/`HIGHLIGHT_LLM_DEVICE_MAP`
  were removed (not applicable to an API call). See `docs/hardware-spec.md` for the
  revised VRAM/GPU accounting and `VAST_GUIDE.md` for updated setup steps.
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
  `YOLOV8_FACE_WEIGHTS_PATH` env var, default `data/models/yolov8x-face-lindevs.pt`
  (**xlarge** variant, largest `lindevs/yolov8-face` publishes — see the "Local-model
  quality pass" note below for why) — source: community repo `lindevs/yolov8-face`
  (WIDERFace-trained, MIT-licensed), must be downloaded onto the vast.ai instance
  manually (or baked into the Docker image — see `Dockerfile`), same category as the
  `ffmpeg` binary being a documented prerequisite rather than a `requirements.txt`
  entry. Inference also runs at `imgsz=1280` (up from Ultralytics' default 640) for
  better small/distant-face recall.
- **Captioning** (`src/captioning/`): for every `HighlightClip` whose reframe is
  `ready`, `python -m src.captioning.run --job-id <ingestion_job_id> [--force]` burns
  short word-burst captions (2-4 words, ~TikTok style, not one-caption-per-sentence)
  onto the finished reframed clip. Reuses data the pipeline already produces rather
  than re-transcribing: `src/transcription/transcriber.py` now passes
  `word_timestamps=True` to faster-whisper (same model/pass, no extra GPU cost) and
  `TranscriptSegment` gained a `words: list[TranscriptWord]` field; the full transcript
  (persisted as `transcript.json`, referenced via `HighlightJob.transcript_path`) is
  reloaded and sliced+time-shifted to each clip's `[start_seconds, end_seconds]` window
  (`src/captioning/subtitles.py::slice_words_to_clip`), then greedily grouped into
  short cues (`build_burst_cues` — closes a cue at 4 words or 1.5s, whichever comes
  first) and written as a plain hand-formatted `.srt` (`write_srt` — no subtitle
  library dependency added). Burn-in is a **separate ffmpeg pass after** reframe's own
  render, not folded into it: reframe's final remux is a pure stream-copy (`-c:v copy`)
  for speed, but subtitle burn-in requires decoding, so captioning re-encodes
  (`-vf "subtitles=...:force_style=..." -c:v libx264 -c:a copy`) from the already-final
  reframed video into `data/captioned/<highlight_clip_id>.mp4` — the true final
  deliverable. Tracked in `caption_jobs`, keyed by `highlight_clip_id` (same
  one-job-per-clip convention as `reframe_jobs`), status
  `pending→generating→rendering→ready`/`failed`. Wired into `src/pipeline/run.py` as
  stage `[5/5]`, after the reframe loop; if a clip's reframe didn't reach `ready`,
  captioning is skipped for that clip rather than raising (checked via the reframe
  loop's per-clip result, not re-queried). No new pip dependency — relies on the
  ffmpeg build having `libass`/the `subtitles` filter compiled in, standard in most
  distro ffmpeg packages (flagged as a one-line gotcha-watch in `VAST_GUIDE.md` in case
  a minimal vast.ai template's ffmpeg build lacks it).

  **Caption font quality fixed (2026-07-28), based on real user feedback from the
  first real captioning run** (timing confirmed correct, font quality was the one
  complaint): `SUBTITLE_STYLE`'s `FontSize` was `14` — tiny on a 1080×1920 output,
  the actual dominant cause, not an encoding artifact. First tried `72`
  (formula-derived guess) but a real local re-render against actual footage showed
  it was *far* too large, covering nearly half the frame — corrected to `36` after
  visually comparing several sizes against real footage, a genuinely
  image-verified value rather than a calculation. Also added
  `FontName=DejaVu Sans Bold` (names the bold weight directly, sidesteps ASS
  `Bold`-flag parsing ambiguity) and bumped `Outline` `2`→`3` to match.
  **`fonts-dejavu-core` is now a required system prerequisite** (`Dockerfile`,
  `VAST_GUIDE.md`, `scripts/entrypoint.sh`) — without it, libass falls back to
  whatever fontconfig finds by default, uncontrolled. Also added
  `original_size=1080x1920` to the `subtitles` filter (explicit libass scaling
  context instead of relying on undocumented auto-detection) and `-crf 18` to the
  libx264 re-encode (default CRF 23 under-serves compact high-contrast text
  glyphs; this is the one ffmpeg call in the project that gets an explicit CRF,
  since it's the final deliverable pass — no other call has this convention).
- Frontend (`frontend/`): React/Vite/TS app, currently just the ingestion view (no more
  tab shell — that was for switching to the now-removed campaign briefs module).
  **UI work is paused** (per user, 2026-07-25) until all backend modules are done —
  later modules (highlights and beyond) are CLI-only for now, no frontend changes
  expected until that's revisited.

**Local-model quality pass (2026-07-26)**, prompted by the same realization behind
the LLM switch above — the 24GB GPU budget is no longer shared with a local LLM, so
every model that still runs *locally* was re-evaluated for whether a higher-quality
option now fits:
- **YOLOv8-face `medium`→`xlarge`** (`yolov8x-face-lindevs.pt`, 68.1M params, the
  largest `lindevs/yolov8-face` publishes) plus `imgsz=1280` (up from 640) for better
  small/distant-face recall. A detection model's VRAM cost is small in absolute terms
  even at xlarge, so this was a clean upgrade with no real downside.
- **Whisper large-v3 stayed the same model** — it's already the largest/most accurate
  standard faster-whisper checkpoint, nothing bigger exists to move up to
  (distil-whisper trades accuracy for speed, the wrong direction here). The real
  lever was an unused inference setting: `vad_filter=True` (`src/transcription/transcriber.py`)
  strips silence before decoding, faster-whisper's own recommended setting for
  real-world audio, reduces hallucinated text. `compute_type` deliberately stayed
  `float16` (not `float32` — doubles VRAM/time for no meaningful WER gain on this
  model) and `beam_size` stayed at the library default of 5 (already optimal; higher
  has diminishing/negative returns) — both considered and explicitly rejected, not
  overlooked.
- **Light-ASD was evaluated for a swap and deliberately kept as-is.** It has no bigger
  official checkpoint (being lightweight is the whole point of the model), so the only
  real "upgrade" would be a different architecture entirely — TalkNet-ASD was
  considered, but research surfaced that Light-ASD's own paper (arXiv 2303.04439)
  claims ~94% mAP on AVA-ActiveSpeaker vs. TalkNet's officially reported ~90-92% mAP
  on the same benchmark. The "bigger" alternative isn't established to actually be
  more accurate, despite requiring a from-scratch vendoring project (new repo, new
  preprocessing, no reuse of the current integration) — not worth that effort for a
  likely lateral-or-worse accuracy move. Revisit only if a genuinely
  benchmark-superior lightweight ASD model surfaces later.
- **ByteTrack excluded** — it's a classical tracking algorithm (Kalman filter +
  Hungarian matching via `supervision`), not a trained model with quality tiers, so
  "model re-selection" doesn't apply to it.
None of this has been verified against real footage yet (same caveat as the
2026-07-25 reframe-module verification and the 2026-07-26 Claude API switch) — needs
a real vast.ai run to confirm actual WER/detection-recall improvement and real VRAM
usage at the new sizes.

**Campaign briefs module removed (2026-07-25, per user direction).** Previously
accepted a brief as pasted text or an uploaded PDF/DOCX/TXT file and stored it as-is,
with structured extraction deferred to a future module that never got built. Decided
not to pursue this direction — removed from `main` (`src/campaign/`,
`src/api/routers/campaign.py`, the campaign frontend view/components/hook, and the
`requests`/`python-multipart` dependencies that existed only for it) rather than left
half-built. The `campaign-module` branch itself is kept on GitHub as archived history,
not deleted.

**Campaign context returns as a prompt, not a module (2026-07-29).** Rather than
resurrecting brief upload/parsing, `IngestionJob` gained a single optional
`campaign_context` text field: a freeform descriptive prompt the user writes/converts
from their own campaign brief **manually, outside the system** — no upload, no
PDF/DOCX parsing, no structured extraction. Set via `--campaign-context "<text>"` or
`--campaign-file <path>` on `python -m src.pipeline.run` (mutually exclusive), or the
`campaign_context` field on `POST /ingestion/jobs`; persists on the job so a
`--job-id` resume doesn't need it repeated. `src/highlights/prompt.py::
build_system_prompt()` layers it onto the base hook-detection system prompt as an
**additional filter, not a replacement** — the model is instructed to still require a
genuine hook first, with campaign relevance breaking ties rather than overriding the
hook bar. This is a much smaller surface than the removed module: no new package, no
new router, no frontend, one nullable column plus a prompt-building change. If a
fuller brief-upload/structured-extraction flow (or Whop integration) comes back
later, treat it as a fresh module built on top of this, not a resurrection of the
removed `src/campaign/` code.

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

Heavy ML dependencies (`faster-whisper`, `ultralytics`, `supervision`, `torch`,
`torchvision`) remain commented out in `requirements.txt` and not installed — deferred
until run on a real vast.ai GPU instance. (Lesson learned early on: a blanket `pip
install -r requirements.txt` once pulled these in transitively because the original
skeleton had them uncommented — always check what's already uncommented before running
a blanket install.) `transformers`/`accelerate`/`bitsandbytes` were removed from this
list entirely on 2026-07-26 — no longer needed anywhere now that highlight detection
uses the Claude API instead of a local LLM. `anthropic` (the Claude SDK) is a normal
unconditional dependency instead — lightweight, no GPU/CUDA involved.

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
- **Highlight LLM (historical — this local-model path no longer exists, see "LLM
  choice switched..." above)**: `generate_candidates()` ran for real against a fake
  transcript with `HIGHLIGHT_LLM_MODEL=Qwen/Qwen2.5-0.5B-Instruct`, `HIGHLIGHT_LLM_4BIT=0`,
  `HIGHLIGHT_LLM_DEVICE_MAP=cpu` — produced syntactically valid JSON (confirms the
  prompt→generation→parsing wiring is correct) but with all-zero timestamps, which
  `parse_candidates()` correctly rejected. This is the small model's capability limit
  (0.5B isn't strong enough to reason about numeric timestamps tied to text), not a
  code bug — the validator did exactly its job. `run_highlight_detection()` run
  end-to-end for real reaches `FAILED`/`no valid highlight candidates survived
  validation` for this same reason, confirming the full chain (transcribe → generate →
  parse) executes correctly up through this small model's actual limitation. Kept here
  as historical record of real local-model verification; superseded once the real
  vast.ai run surfaced the context-window bug that motivated the Claude API switch.
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
`transcriber.py`/`face_detector.py` now guarded with `if torch.cuda.is_available()`.
Env vars for this same purpose, defaulting to production behavior when unset:
`WHISPER_MODEL_SIZE` (default `large-v3`), `WHISPER_DEVICE` (default `cuda`). None of
this changes behavior on the real vast.ai deployment target (CUDA always available
there) — it only makes local CPU verification possible. (`HIGHLIGHT_LLM_4BIT`/
`HIGHLIGHT_LLM_DEVICE_MAP` mentioned in older versions of this doc no longer exist —
removed when highlight detection moved to the Claude API, see "LLM choice switched..."
above; `HIGHLIGHT_LLM_MODEL` still exists but now selects a Claude model name.)

**Still unverified even after this pass:** actual production-scale model quality —
whisper large-v3 transcription accuracy, real Claude highlight judgment on a full-length
video (the 57-min vast.ai test ran on the old local-LLM path, before this switch),
YOLOv8-face/ByteTrack/Light-ASD *accuracy* against real footage (vs. just "doesn't
crash" against a synthetic video with zero real faces) — needs either a real
face+speech test clip locally or another real vast.ai run with the current code.
**Captioning is entirely unverified against real speech** too — local dev has no real
face+speech test clip, so word-burst grouping/timing and the actual on-screen caption
look have only been checked against hand-written fake transcripts, and the ffmpeg
`subtitles` burn-in has only been confirmed to run (not necessarily produce
good-looking output) locally; also needs a real vast.ai run to confirm the rented
instance's ffmpeg build actually has `libass` compiled in.

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

**A real vast.ai run already happened (2026-07-25, 57-min video, RTX 3090-class,
`vast-ai-e2e-prep` branch)** — all four stages ran for real end-to-end, but it
surfaced the context-window bug described above (highlight-detection LLM stage). Fixed
by switching to the Claude API (2026-07-26) plus quality upgrades (whisper `float16`,
YOLOv8-face `medium`, later `xlarge`) using the VRAM the local LLM no longer needs.
**Captioning and the further YOLOv8-face `xlarge`/`imgsz=1280`/whisper `vad_filter`
pass (also 2026-07-26) are new since that run and have never been exercised on
vast.ai at all.** None of this is yet re-verified with a real run — that's next:

1. Re-run the full pipeline end-to-end on vast.ai with the current code
   (`vast-ai-e2e-prep` branch, updated `VAST_GUIDE.md`): confirm the Claude API
   actually produces well-distributed, genuinely hook-worthy candidates across a full
   long video (not just the first minute); confirm whisper `float16`+`vad_filter` and
   YOLOv8-face `xlarge`@`imgsz=1280` fit comfortably in the RTX 3090 24GB budget
   alongside the freed-up headroom (re-check the real logged VRAM against the
   ~10-13GB re-estimate in `docs/hardware-spec.md`); confirm real per-video Claude
   API cost against the ~$0.20-0.31 estimate; confirm the rented instance's ffmpeg
   has `libass`/`subtitles`-filter support and that real word-burst caption
   timing/grouping actually looks good against real speech, not just hand-written
   fake transcripts; confirm the `vad_filter`/`xlarge` changes actually measurably
   improve transcript/detection quality rather than just costing more compute for no
   real benefit.
2. Get a real short face+speech test clip (e.g. a webcam recording) to close the one
   remaining local-verification gap: actual YOLOv8-face/ByteTrack/Light-ASD behavior
   against real content, still only checked for "doesn't crash" against a synthetic
   video with zero real faces, or against the first vast.ai run's footage which wasn't
   independently reviewed frame-by-frame.
3. Merge `vast-ai-e2e-prep` into `main` once the re-run above confirms the fixes work
   — not merged yet, per the project's need-driven (not completion-order) merge
   policy.

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
