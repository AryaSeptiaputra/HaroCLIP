# HaroClip — vast.ai End-to-End Test Guide

Step-by-step for running the full pipeline (ingestion → download → highlight
detection → dynamic-crop reframe) on a rented vast.ai GPU instance. Written for a
**single, credit-constrained run** — follow it in order rather than improvising.

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
- **GPU**: 12–16GB is likely enough (e.g. RTX 3060 12GB, RTX 4070) — real peak VRAM
  usage is ~7–8GB given every stage loads/frees its model sequentially, not
  concurrently. A 24GB card (RTX 4090/3090) is a safety margin, not a requirement, and
  costs more per hour. Pick based on your remaining budget.
- **vCPU/RAM**: 8+ cores, 32GB+ RAM (ffmpeg decode/encode benefits from multi-core).
- **Storage**: 100GB+ (source video + model weights + output clips).
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
# multi-GB download this approach specifically avoids. Everything else:
pip install faster-whisper transformers accelerate bitsandbytes \
    ultralytics supervision python_speech_features

mkdir -p data/models
curl -L "https://github.com/lindevs/yolov8-face/releases/latest/download/yolov8n-face-lindevs.pt" \
    -o data/models/yolov8n-face-lindevs.pt
```

Light-ASD's weights don't need a separate download — they're vendored and committed in
git, already present after `git clone`.

## 3. Run the pipeline

```bash
python3 -m src.pipeline.run --url "https://youtu.be/q44ozTxnU8A?si=wR5jQi7c9vxFSWQG"
```

This chains all four stages (ingestion → processing → highlights → reframe)
automatically, printing a `[1/4]`...`[4/4]` progress summary and, on success, the
ingestion job id plus where results landed:

```
=== DONE. ingestion job id: <uuid> ===
logs:        data/logs/<uuid>/
final clips: data/reframed/
```

Whisper-large-v3 and Qwen2.5-7B-Instruct auto-download from Hugging Face on first use
(a few GB total, one-time per instance) — the very first run will be slower than
subsequent ones for this reason alone, separate from actual processing time. If a
download fails with a `MemoryError`/Rust panic, see "Known gotchas" below.

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

- `data/logs/<ingestion_job_id>/` — four files (`ingestion.log`, `processing.log`,
  `highlights.log`, `reframe.log`). **These are what to bring back for quality
  analysis** — they contain the full transcript, the full raw LLM response, why each
  candidate was accepted/rejected, detection/tracking counts, which active-speaker
  method actually ran (real Light-ASD vs. heuristic fallback — check this, it tells you
  whether the ASD deps/weights actually worked), and VRAM usage after each model load.
- `data/reframed/*.mp4` — the final dynamic vertical-crop clips.
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
  ~7–8GB peak estimate, note if it's meaningfully different so the doc can be corrected.

## Known gotchas (from local dev this session — may or may not reproduce on vast.ai)

- `huggingface_hub`'s `hf-xet` fast-download accelerator crashed with a low-level
  memory error on the local Windows dev machine. If model downloads fail with a
  `MemoryError`/Rust panic, retry with `HF_HUB_DISABLE_XET=1` set in the environment.
- If a `pip install` or download seems to hang or die immediately for no clear reason,
  retry once — this was a local Windows-sandbox quirk in earlier testing, unlikely on
  a real Linux instance, but worth ruling out quickly rather than assuming something
  is fundamentally broken.
- `bitsandbytes` (needed for 4-bit Qwen2.5-7B) has historically had rockier Windows
  support but should install cleanly on vast.ai's Linux environment — not expected to
  be an issue here, noted just in case.

## About the Dockerfile

`Dockerfile` in the repo root builds a fully self-contained image (CUDA base + every
dependency + baked-in YOLOv8-face weights) and was validated structurally (build
started successfully, layer-by-layer) but not completed end-to-end locally due to
local bandwidth. It's kept for later — e.g. pushing a prebuilt image to a registry once
one exists, for faster/more reproducible instance boots — but isn't part of this first
test's critical path.

## Bring back to Claude

Once you have `data/logs/<ingestion_job_id>/` locally, hand the four log files back for
analysis — that's the whole point of the logging added this session. Also mention:
total wall-clock time, which GPU/VRAM you actually rented, which template you used, and
whether setup or the run itself took unexpectedly long, so cost/time estimates in
`docs/hardware-spec.md` can be corrected with real numbers instead of guesses.
