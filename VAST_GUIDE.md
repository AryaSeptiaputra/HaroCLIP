# HaroClip — vast.ai End-to-End Test Guide

Step-by-step for running the full pipeline (ingestion → download → highlight
detection → dynamic-crop reframe → caption burn-in) on a rented vast.ai GPU instance.
Written for a **single, credit-constrained run** — follow it in order rather than
improvising.

Branch to test: `vast-ai-e2e-prep` (not yet merged to `main` — merge only after this
test succeeds, so any fixes needed land in the same branch).

**Setup approach: install directly on the instance, no Docker.** A `Dockerfile` exists
in the repo for later reproducibility, but for this first real run it's simpler and
faster to pick a vast.ai template that already has PyTorch+CUDA installed and add our
remaining dependencies on top — avoids re-downloading a multi-GB CUDA base image and
torch wheel inside the instance, and avoids depending on vast.ai's nested-Docker
support (not guaranteed on every template).

## 1. Rent the instance

See `docs/hardware-spec.md` for the full breakdown. Short version:

- **Template: pick a "PyTorch" template with CUDA 12.x already installed.** Search
  vast.ai's template gallery for "PyTorch" — most official/popular ones qualify. This
  means `torch` is already present (skip installing it — see step 2) and the CUDA
  driver is already matched, which is the fiddliest part to get right manually.
- **Python version: 3.10+ is fine, doesn't need to be exactly 3.13.** The codebase's
  local dev venv uses 3.13, but nothing in it is 3.13-exclusive (just modern `X | None`
  type hints, which work from 3.10 on) — use whatever `python3` the template ships.
- **GPU: RTX 4090/3090 24GB** (decided 2026-07-26, not downsized). Real peak VRAM is
  ~10–11GB (whisper `float16` is now the single biggest local stage, since highlight
  detection runs on the Claude API instead of a local GPU model — see
  `docs/hardware-spec.md`) so a 12-16GB card would technically fit, but the 24GB tier
  is kept deliberately: the VRAM the local LLM no longer needs was redirected into
  quality upgrades (whisper `float16`, YOLOv8-face `medium`) rather than downsizing for
  cost. Don't pick a smaller card to save money here — that would undo the deliberate
  quality tradeoff already made.
- **vCPU/RAM**: 8+ cores, 32GB+ RAM (ffmpeg decode/encode benefits from multi-core).
- **Storage: ~25-30GB is enough, not 100GB.** Real breakdown for a ~1hr 1080p test
  video: source video ~0.5-2.5GB, whisper large-v3 checkpoint ~3GB (same download size
  regardless of int8/float16 — quantization affects VRAM at inference, not the
  on-disk checkpoint), pip packages ~3-5GB, weights/outputs <1GB. **No local LLM
  checkpoint to download at all** — highlight detection is a Claude API call now, not
  a local model, which removes what used to be the single largest download
  (Qwen2.5-7B-Instruct, ~15GB fp16) entirely. ~10GB real usage; 25-30GB gives
  comfortable margin. Only go bigger if testing a much longer video or planning
  multiple videos per instance.
- **On-demand, not interruptible/spot** — a preempted mid-render job wastes the whole
  run.
- Filter by GPU **and** vCPU/RAM together — host specs vary between listings with the
  same GPU model.

## 2. SSH in, verify the base template, install remaining deps

**First, before anything else**, confirm the template actually has a working
CUDA-enabled torch — fail fast here rather than after a long setup:

```bash
nvidia-smi
python3 -c "import torch; print(torch.__version__, 'cuda:', torch.cuda.is_available())"
```

If `torch.cuda.is_available()` isn't `True`, stop and pick a different template —
nothing downstream will work right.

Then clone and install:

```bash
git clone https://github.com/AryaSeptiaputra/HaroCLIP.git
cd HaroCLIP
git checkout vast-ai-e2e-prep

apt-get update && apt-get install -y ffmpeg   # most PyTorch templates don't include this

pip install -r requirements.txt
# torch is already installed by the template — do NOT reinstall it, that's the
# multi-GB download this approach specifically avoids. Everything else (note:
# no transformers/accelerate/bitsandbytes needed anymore — highlight detection
# is a Claude API call, not a local model):
pip install faster-whisper ultralytics supervision python_speech_features

mkdir -p data/models
curl -L "https://github.com/lindevs/yolov8-face/releases/latest/download/yolov8m-face-lindevs.pt" \
    -o data/models/yolov8m-face-lindevs.pt
```

Light-ASD's weights don't need a separate download — they're vendored and committed in
git, already present after `git clone`.

**Set your Claude API key** — required for the highlight-detection stage:

```bash
export ANTHROPIC_API_KEY=<your-key>
```

Get it from [console.anthropic.com](https://console.anthropic.com/settings/keys). Same
handling rules as `HF_TOKEN` below: never commit it, never bake it into the Dockerfile
as a static `ENV` line (image layers are inspectable) — export it in the shell only, or
put it in a gitignored `.env` file on the instance.

**Then apply two fixes confirmed necessary on a real run** (do these now, proactively
— both were hit during actual testing, not hypothetical):

```bash
# 1. faster-whisper/CTranslate2 needs cuBLAS/cuDNN on the loader path — PyTorch bundles
#    its own copy privately, not visible to other libraries. Without this: "Library
#    libcublas.so.12 is not found or cannot be loaded".
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
export LD_LIBRARY_PATH=`python3 -c 'import os; import nvidia.cublas.lib, nvidia.cudnn.lib; print(os.path.dirname(nvidia.cublas.lib.__file__) + ":" + os.path.dirname(nvidia.cudnn.lib.__file__))'`

# 2. huggingface_hub's Rust-based hf-xet fast-download accelerator is unreliable —
#    crashed with "Internal Writer Error: Background writer channel closed" downloading
#    a model on a prior run. Force the plain Python downloader instead. Still relevant
#    even though the LLM stage moved off Hugging Face entirely — whisper's checkpoint
#    still downloads through the same hf-xet path.
export HF_HUB_DISABLE_XET=1

# Optional but recommended: avoids the "unauthenticated requests" HF Hub rate-limit
# warning. Read-only token from huggingface.co/settings/tokens.
export HF_TOKEN=<your-read-only-token>
```

**Both `export` lines only last for the current shell session** — if your SSH
connection drops and you reconnect, run them again before resuming with `--job-id`
(or add them to `~/.bashrc` if you expect multiple sessions).

## 3. Run the pipeline

```bash
python3 -m src.pipeline.run --url "https://youtu.be/q44ozTxnU8A?si=wR5jQi7c9vxFSWQG"
```

This chains all five stages (ingestion → processing → highlights → reframe →
captioning) automatically, printing a `[1/5]`...`[5/5]` progress summary and, on
success, the ingestion job id plus where results landed:

```
=== DONE. ingestion job id: <uuid> ===
logs:          data/logs/<uuid>/
reframed clips: data/reframed/
captioned clips (final deliverable): data/captioned/
```

Whisper-large-v3 auto-downloads from Hugging Face on first use (~3GB, one-time per
instance) — the very first run will be slower than subsequent ones for this reason
alone, separate from actual processing time. No local LLM checkpoint to wait on
anymore — highlight detection calls the Claude API directly.

### If it fails partway through

The printed error tells you which stage failed and gives you the ingestion job id.
**Don't start over from `--url`** — that re-downloads the source video and re-runs
every already-successful stage. Instead:

```bash
python3 -m src.pipeline.run --job-id <uuid>
```

Every stage is idempotent (short-circuits if already `ready` unless you also pass
`--force`), so this safely resumes from wherever it actually stopped.

## 4. Collect results before destroying the instance

Everything lands under `HaroCLIP/data/` (set via `DATA_DIR`, defaults to `./data`
relative to wherever you ran the command):

- `data/logs/<ingestion_job_id>/` — five files (`ingestion.log`, `processing.log`,
  `highlights.log`, `reframe.log`, `captioning.log`). **These are what to bring back
  for quality analysis** — they contain the full transcript, the full raw LLM response,
  why each candidate was accepted/rejected, detection/tracking counts, which
  active-speaker method actually ran (real Light-ASD vs. heuristic fallback — check
  this, it tells you whether the ASD deps/weights actually worked), VRAM usage after
  each model load, and (in `captioning.log`) word/cue counts per clip and ffmpeg
  burn-in duration.
- `data/captioned/*.mp4` — **the final deliverable**: reframed clips with captions
  burned in.
- `data/reframed/*.mp4` — the dynamic vertical-crop clips *before* captioning (useful
  to compare against `data/captioned/` if caption placement/timing looks off).
- `data/captions/*.srt` — the generated subtitle files, one per clip — open these
  directly if you want to check caption text/timing without opening the burned-in
  video.
- `data/clips/<ingestion_job_id>/` — the intermediate static clips (before reframing).
- `data/haroclip.db` — SQLite DB with full job status/metadata if useful.

```bash
# from your local machine, not the instance:
scp -r -P <port> root@<instance-ip>:~/HaroCLIP/data ./haroclip-results
```

(Adjust the path/port to whatever vast.ai's SSH connection details show for your
instance.) Then **destroy the instance** — don't leave it running once you have what
you need.

## 5. What to check in the logs afterward

- `reframe.log`: search for `method=` — `method=light-asd` means real ASD scoring ran;
  `method=heuristic` means it fell back (check the `reason=` on that line — usually
  either `ImportError` meaning deps weren't actually installed, or a scoring
  exception).
- `highlights.log`: compare the full transcript against which candidates were kept —
  did the LLM pick genuinely interesting moments, or did most candidates get rejected
  by validation (check `parse_candidates` rejection reasons)?
- Every log's `VRAM after ...` lines — compare against `docs/hardware-spec.md`'s
  re-corrected ~10–11GB peak estimate (whisper `float16` is now the single biggest
  local stage), note if it's meaningfully different so the doc can be corrected.
- `highlights.log`'s token-usage line (input/output tokens from the Claude API
  response) — compare the real per-video cost against the ~$0.20–0.31 estimate in
  `docs/hardware-spec.md`.
- `data/captioned/*.mp4`: actually watch a clip (or at least seek through it) — this
  is the one stage that's never been checked against real speech, only hand-written
  fake transcripts locally. Confirm captions are legible, correctly timed to the
  audio, and grouped at a sensible 2-4-word burst size (`captioning.log` has the
  word/cue counts if the grouping looks off).

## Known gotchas

Confirmed on a real vast.ai run (already folded into step 2 above — listed here for
reference/in case they resurface):

- `libcublas.so.12 is not found or cannot be loaded` at the whisper transcription
  step — fixed by installing `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` and setting
  `LD_LIBRARY_PATH` to their install location (step 2).
- `Internal Writer Error: Background writer channel closed` downloading a model from
  Hugging Face — `hf-xet`'s Rust downloader crashing. Fixed by `HF_HUB_DISABLE_XET=1`
  (step 2). If it recurs even with that set, `pip uninstall -y hf-xet` to remove it
  entirely. This was hit downloading the old local Qwen2.5-7B checkpoint, but the same
  `hf-xet` path is still used for whisper's checkpoint, so the fix stays relevant.

Not yet confirmed either way on vast.ai (worked locally, but that's a different ffmpeg
build):

- **Captioning's burn-in relies on the ffmpeg build having `libass` compiled in** (the
  `subtitles` filter). This is standard on Ubuntu's `apt` ffmpeg package and most
  distro/static builds, but if `captioning.log` shows an `ffmpeg caption burn-in
  failed` error mentioning `subtitles` or `No such filter`, that's the cause — check
  with `ffmpeg -filters | grep subtitles` and, if missing, reinstall ffmpeg via `apt`
  rather than whatever the base template shipped.

## About the Dockerfile

`Dockerfile` in the repo root builds a fully self-contained image (CUDA base + every
dependency + baked-in YOLOv8-face weights) and was validated structurally (build
started successfully, layer-by-layer) but not completed end-to-end locally due to
local bandwidth. It's kept for later — e.g. pushing a prebuilt image to a registry once
one exists, for faster/more reproducible instance boots — but isn't part of this first
test's critical path.

## Bring back to Claude

Once you have `data/logs/<ingestion_job_id>/` locally, hand the five log files back for
analysis — that's the whole point of the logging added this session. Also mention:
total wall-clock time, which GPU/VRAM you actually rented, which template you used, and
whether setup or the run itself took unexpectedly long, so cost/time estimates in
`docs/hardware-spec.md` can be corrected with real numbers instead of guesses.
