# HaroClip — Hardware Spec (vast.ai rental)

Compute is rented on-demand from **vast.ai** rather than run on local hardware.
This doc records the sizing decision so it doesn't need to be re-derived each session.

## Assumptions

- Transcription: faster-whisper **large-v3, int8** (CTranslate2)
- Source video worst case: 1–3 hour VOD/podcast, 1080p
- Use case: production, single-user personal use (not multi-tenant SaaS)
- Budget: ~$0.30–0.60/hr

## Per-stage resource estimate

| Stage | Component | VRAM (est.) | Notes |
|---|---|---|---|
| Transcription | faster-whisper large-v3, int8 | ~4.5–5 GB | int8 quantized via CTranslate2; ~20–40x realtime on RTX 3090/4090 |
| Face detection | YOLOv8-face (nano/small) | ~1.5–2 GB | Cost scales with sampled frame count, not full framerate |
| Active Speaker Detection | Light-ASD | ~1–2 GB | Per face-track, low overhead vs whisper |
| Tracking | ByteTrack | 0 (CPU-only) | Kalman filter + Hungarian matching |
| Rendering | ffmpeg + NVENC | ~0.5–1 GB | Hardware encode, much faster than CPU x264 |
| Overhead | CUDA context (models resident together) | ~1–2 GB | If all models kept loaded to avoid reload between stages |

**Peak VRAM (all models resident, worst case): ~9–12 GB.**

24GB is kept as the target (not downgraded to 16GB) for batch-size headroom,
running stages concurrently instead of strictly sequential, and future model upgrades.

## GPU recommendation

- **Primary: RTX 4090 24GB** — best price/perf on vast.ai in this budget range, modern NVENC (AV1/H.264/H.265)
- **Fallback: RTX 3090 24GB** — same VRAM, usually cheaper, older NVENC, still plenty of compute
- **Avoid**: A100/H100 (massive overkill, priced out of budget), A5000/A6000 (no meaningful benefit for this workload, usually worse $/hr than 4090)

## CPU / RAM / Storage

- vCPU: 8–16 cores (ffmpeg decode/encode + preprocessing benefits from multi-core)
- RAM: 32GB minimum, 64GB comfortable (decode buffers for long 1080p video)
- Storage: 100GB minimum
  - Source video: ~3–8GB/hour (H.264) → 3hr video ≈ 10–25GB
  - Model weights: whisper large-v3 int8 (~3GB) + YOLOv8-face (tens of MB) + Light-ASD (<500MB)
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

## Next validation step

Once the first vertical slice (highlight detection → ffmpeg render) runs on a real
vast.ai instance, compare actual VRAM usage and wall-clock time against this doc and
revise the numbers if they're off.
