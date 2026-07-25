# HaroClip — vast.ai End-to-End Test Guide

Step-by-step for running the full pipeline (ingestion → download → highlight
detection → dynamic-crop reframe) on a rented vast.ai GPU instance. Written for a
**single, credit-constrained run** — follow it in order rather than improvising.

Branch to test: `vast-ai-e2e-prep` (not yet merged to `main` — merge only after this
test succeeds, so any fixes needed land in the same branch).

## 1. Rent the instance

See `docs/hardware-spec.md` for the full breakdown. Short version:

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

## 2. SSH in, clone, build

```bash
git clone https://github.com/AryaSeptiaputra/HaroCLIP.git
cd HaroCLIP
git checkout vast-ai-e2e-prep

docker build -t haroclip .
```

The build installs CUDA 12.4 + Python 3.13, `ffmpeg`, every dependency (including the
heavy ML stack — torch/faster-whisper/transformers/accelerate/bitsandbytes/
ultralytics/supervision/python_speech_features), and downloads the YOLOv8-face
weights. **This step alone can take 20–40+ minutes** depending on the instance's
network speed — the CUDA base image and torch wheel are several GB each. Budget for
this before your first real job starts; it's a one-time cost per instance (not
per-video), so if you expect to test more than one video, keep the instance around
between runs rather than rebuilding.

## 3. Run the container

```bash
docker run --gpus all -v $(pwd)/data:/workspace/data -it haroclip bash
```

`-v $(pwd)/data:/workspace/data` is important — it makes `data/` persist on the host
filesystem instead of only inside the container, so results survive even if the
container is removed. **Always use this flag.**

## 4. Run the pipeline

Inside the container:

```bash
python3.13 -m src.pipeline.run --url "https://youtu.be/q44ozTxnU8A?si=wR5jQi7c9vxFSWQG"
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
subsequent ones for this reason alone, separate from actual processing time.

### If it fails partway through

The printed error tells you which stage failed and gives you the ingestion job id.
**Don't start over from `--url`** — that re-downloads the source video and re-runs
every already-successful stage. Instead:

```bash
python3.13 -m src.pipeline.run --job-id <uuid>
```

Every stage is idempotent (short-circuits if already `ready` unless you also pass
`--force`), so this safely resumes from wherever it actually stopped.

## 5. Collect results before destroying the instance

Everything you need is under `data/` on the **host** (thanks to the `-v` mount in step
3), so you don't need to be inside the container for this:

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
# from your local machine, not inside the container:
scp -r -P <port> root@<instance-ip>:/workspace/HaroCLIP/data ./haroclip-results
```

(Adjust the path/port to whatever vast.ai's SSH connection details show for your
instance.) Then **destroy the instance** — don't leave it running once you have what
you need.

## 6. What to check in the logs afterward

- `reframe.log`: search for `method=` — `method=light-asd` means real ASD scoring ran;
  `method=heuristic` means it fell back (check the `reason=` on that line — usually
  either `ImportError` meaning deps weren't actually installed in the image, or a
  scoring exception).
- `highlights.log`: compare the full transcript against which candidates were kept —
  did the LLM pick genuinely interesting moments, or did most candidates get rejected
  by validation (check `parse_candidates` rejection reasons)?
- Every log's `VRAM after ...` lines — compare against `docs/hardware-spec.md`'s
  ~7–8GB peak estimate, note if it's meaningfully different so the doc can be corrected.

## Known gotchas (from local dev this session — may or may not reproduce on Linux/vast.ai)

- `huggingface_hub`'s `hf-xet` fast-download accelerator crashed with a low-level
  memory error on the local Windows dev machine. If model downloads fail with a
  `MemoryError`/Rust panic, retry with `HF_HUB_DISABLE_XET=1` set in the environment.
- If a `pip install` or download inside the container seems to hang or die
  immediately for no clear reason, retry once — this was a local Windows-sandbox
  quirk, unlikely on a real Linux instance, but worth ruling out quickly rather than
  assuming something is fundamentally broken.

## Bring back to Claude

Once you have `data/logs/<ingestion_job_id>/` locally, hand the four log files back for
analysis — that's the whole point of the logging added this session. Also mention:
total wall-clock time, which GPU/VRAM you actually rented, and whether the build step
or the run step took unexpectedly long, so cost/time estimates in
`docs/hardware-spec.md` can be corrected with real numbers instead of guesses.
