# HaroClip — Hardware Spec (vast.ai rental)

Compute is rented on-demand from **vast.ai** rather than run on local hardware.
This doc records the sizing decision so it doesn't need to be re-derived each session.

## Assumptions

- Transcription: faster-whisper **large-v3, int8** (CTranslate2)
- Highlight detection LLM: **Qwen2.5-7B-Instruct, 4-bit** (`transformers`+`bitsandbytes`)
- Source video worst case: 1–3 hour VOD/podcast, 1080p
- Use case: production, single-user personal use (not multi-tenant SaaS)
- Budget: ~$0.30–0.60/hr

## Per-stage resource estimate

| Stage | Component | VRAM (est.) | Notes |
|---|---|---|---|
| Transcription | faster-whisper large-v3, int8 | ~4.5–5 GB | int8 quantized via CTranslate2; ~20–40x realtime on RTX 3090/4090 |
| Highlight detection | Qwen2.5-7B-Instruct, 4-bit | ~5–6 GB | `transformers`+`bitsandbytes`; **missing from earlier versions of this doc** (added 2026-07-25, after `highlights-module` shipped) |
| Face detection | YOLOv8-face (nano/small) | ~1.5–2 GB | Cost scales with sampled frame count, not full framerate |
| Active Speaker Detection | Light-ASD | ~1–2 GB | Per face-track, low overhead vs whisper |
| Tracking | ByteTrack | 0 (CPU-only) | Kalman filter + Hungarian matching |
| Rendering | ffmpeg (libx264, CPU) | 0 | NVENC not used yet — see note below |
| Overhead | CUDA context (fresh per model load) | ~1–1.5 GB | Paid once per stage, not cumulative — see below |

**Peak VRAM corrected (2026-07-25): ~7–8 GB, not the previous 9–12 GB estimate.**
The previous estimate assumed stages might run *concurrently* ("if all models kept
loaded to avoid reload between stages"). That was never how it was actually built:
every stage implemented so far (`transcriber.py`, `highlights/llm.py`,
`face_detector.py`, `asd_scoring.py`) explicitly frees its model
(`del model` + `torch.cuda.empty_cache()`, guarded by `torch.cuda.is_available()`)
**before the next stage loads**. Real peak VRAM at any instant is therefore
`max(single biggest stage) + CUDA context overhead` — the biggest single stage is
Qwen2.5-7B 4-bit or whisper large-v3 (~5–6GB either way) plus ~1–1.5GB overhead ≈
**~7–8GB**, not a sum of every stage's estimate. Confirmed structurally by the vendored
Light-ASD `asd.py`'s device handling and every module's own `close()`/cleanup code —
still needs confirming against **real observed numbers** from a production run (each
stage now logs `torch.cuda.memory_allocated()`/`memory_reserved()` right after its
model loads — see `src/utils/logging.py`'s `log_vram()` — check `data/logs/<job_id>/`
after a real run and reconcile against this doc).

**ffmpeg rendering uses CPU `libx264`, not NVENC**, despite NVENC being lower VRAM and
faster — a deliberate choice to avoid introducing an unverified behavior change (ffmpeg
build flags, driver passthrough) right before a credit-constrained real test. Revisit
once the pipeline is proven end-to-end for real.

## GPU recommendation

Given the corrected ~7–8GB peak (see above), a 24GB card is a **safety margin, not a
hard requirement** — worth weighing against vast.ai cost, especially with limited
credit:

- **Economical: 12–16GB** (e.g. RTX 3060 12GB, RTX 4070 12GB) — likely sufficient given
  verified sequential loading, meaningfully cheaper per hour. Recommended first choice
  if minimizing cost matters more than safety margin.
- **Safe margin: RTX 4090/3090 24GB** — same as originally documented, more headroom
  for batch-size experiments or future concurrent-stage optimization, modern NVENC if
  that gets adopted later. Costs more per hour.
- **Avoid**: A100/H100 (massive overkill, priced out of budget), A5000/A6000 (no
  meaningful benefit for this workload, usually worse $/hr than 4090).

Final call is the user's when actually renting — this is a data-driven recommendation
based on verified sequential-loading behavior, not a hard rule.

## CPU / RAM / Storage

- vCPU: 8–16 cores (ffmpeg decode/encode + preprocessing benefits from multi-core)
- RAM: 32GB minimum, 64GB comfortable (decode buffers for long 1080p video)
- Storage: 100GB minimum
  - Source video: ~3–8GB/hour (H.264) → 3hr video ≈ 10–25GB
  - Model weights: whisper large-v3 int8 (~3GB) + Qwen2.5-7B-Instruct 4-bit (~4–5GB) +
    YOLOv8-face (tens of MB) + Light-ASD (<10MB, vendored in git) — whisper/Qwen
    auto-download to the HuggingFace cache on first use, no manual step
  - Output clips: tens–hundreds of MB each

Filter vast.ai listings by GPU **and** vCPU/RAM together — host specs vary between
listings with the same GPU model.

## Billing strategy

- **On-demand instances, not interruptible/spot** — job shouldn't get preempted mid-render
- **Don't rent 24/7.** Boot the instance only when a video needs processing (manual trigger,
  or a script that boots the vast.ai instance via their API when a Whop webhook lands),
  destroy after the job completes
- **Persist model weights** via a vast.ai template/saved image or network volume to avoid
  re-downloading ~5GB of weights on every instance boot

## Cost estimate

- RTX 4090 24GB + 8–16 vCPU/32–64GB RAM + 100GB disk: **~$0.35–0.55/hr** on the vast.ai marketplace
- Wall-clock per video (1–3hr source, 1080p): transcription a few minutes, detection/ASD/tracking
  ~10–20 min (sampling-rate dependent), rendering a few minutes → **~20–40 min total**
- Cost per video: **~$0.15–0.30**

> vast.ai pricing is a live marketplace and fluctuates with supply/demand — these are
> ballpark estimates; check actual listing prices before renting.

## Running on vast.ai

**See `VAST_GUIDE.md` (repo root) for the actual step-by-step walkthrough** — rent →
template choice → setup → run → collect results → troubleshooting. Summary of the
approach decided there: for the first real test, install directly onto a vast.ai
instance rented from a **PyTorch-preinstalled template** (CUDA + torch already
present, so no multi-GB CUDA base image or torch wheel to download inside the
instance), rather than a from-scratch Docker build — simpler, faster to get running,
and doesn't depend on vast.ai's nested-Docker support being available on a given
template.

`Dockerfile` (repo root) still exists and builds the full self-contained production
image (CUDA 12.4 + Python 3.13 + every dependency + baked-in YOLOv8-face weights) — it
was structurally validated (build starts and proceeds correctly, layer by layer) but
not run to completion locally due to bandwidth. Kept for later, e.g. once a prebuilt
image is worth pushing to a registry for faster/more reproducible instance boots — not
on the critical path for the first real test.

## Next validation step

This is now in progress: the first real vast.ai run of the full pipeline (ingestion →
processing → highlight detection → reframe) via `src.pipeline.run`. Every stage logs
timings and `torch.cuda.memory_allocated()`/`memory_reserved()` right after its model
loads (`data/logs/<ingestion_job_id>/<stage>.log`) — after the run, compare those real
numbers against this doc's estimates (particularly the corrected ~7–8GB peak-VRAM
claim above) and revise if they're off.
