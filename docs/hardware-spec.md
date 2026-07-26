# HaroClip — Hardware Spec (vast.ai rental)

Compute is rented on-demand from **vast.ai** rather than run on local hardware.
This doc records the sizing decision so it doesn't need to be re-derived each session.

## Assumptions

- Transcription: faster-whisper **large-v3, float16, `vad_filter=True`** (CTranslate2)
  — `int8`→`float16` on 2026-07-26, `vad_filter` added later the same day (already the
  largest standard whisper checkpoint, so the remaining quality lever is inference
  settings, not a bigger model — see `CLAUDE.md`'s "Local-model quality pass")
- Highlight detection: **Claude API** (`anthropic` SDK) — no local model, no local
  GPU/VRAM use at all. Switched from local Qwen2.5-7B-Instruct 4-bit on 2026-07-26,
  see `CLAUDE.md`'s "Current status" for the full rationale (context-window bug found
  on the first real vast.ai run)
- Face detection: YOLOv8-face **xlarge** variant at `imgsz=1280` — `nano`→`medium` on
  2026-07-26, then `medium`→`xlarge` later the same day once the medium-vs-larger
  headroom was re-examined (a detection model's VRAM cost is small even at xlarge)
- Captioning: ffmpeg `subtitles` filter burn-in (added 2026-07-26) — CPU-bound
  re-encode, no GPU/VRAM use, see `CLAUDE.md`'s "Captioning" bullet
- Source video worst case: 1–3 hour VOD/podcast, 1080p
- Use case: production, single-user personal use (not multi-tenant SaaS)
- Budget: ~$0.30–0.60/hr

## Per-stage resource estimate

| Stage | Component | VRAM (est.) | Notes |
|---|---|---|---|
| Transcription | faster-whisper large-v3, **float16**, `vad_filter=True` | ~9–10 GB | Upgraded from `int8` (~4.5–5GB) on 2026-07-26 — roughly double, spending the VRAM the local LLM no longer needs on transcription precision instead; `vad_filter` adds no VRAM (it's a pre-decoding silence-stripping pass); ~10–20x realtime on RTX 3090/4090 |
| Highlight detection | **Claude API** (Sonnet-tier) | **0 GB (no local GPU use)** | Runs over the network, not on the rented GPU at all — see `CLAUDE.md` for why this replaced the local Qwen2.5-7B-Instruct 4-bit (~5–6GB) stage on 2026-07-26 |
| Face detection | YOLOv8-face, **xlarge**, `imgsz=1280` | ~5–8 GB | Upgraded `nano`→`medium`→**xlarge** (68.1M params, largest `lindevs/yolov8-face` publishes) on 2026-07-26, plus `imgsz` 640→1280 for better small-face recall. Estimate is a rough scale-up from medium's ~3–4GB, **not yet measured** — detection-model VRAM doesn't scale linearly with params the way LLM VRAM does, so treat this range loosely until a real run's logs confirm it. Cost scales with sampled frame count, not full framerate |
| Active Speaker Detection | Light-ASD | ~1–2 GB | Per face-track, low overhead vs whisper |
| Tracking | ByteTrack | 0 (CPU-only) | Kalman filter + Hungarian matching |
| Rendering | ffmpeg (libx264, CPU) | 0 | NVENC not used yet — see note below |
| Captioning | ffmpeg `subtitles` filter burn-in (libass, CPU) | 0 | Added 2026-07-26 — re-encode pass, CPU-bound like rendering; requires the ffmpeg build to have `libass` compiled in (standard on most distro builds) |
| Overhead | CUDA context (fresh per model load) | ~1–1.5 GB | Paid once per stage, not cumulative — see below |

**Peak VRAM re-corrected again (2026-07-26): ~10–13 GB estimated, not the prior
~10–11 GB.** Every stage implemented so far still explicitly frees its model
(`del model` + `torch.cuda.empty_cache()`, guarded by `torch.cuda.is_available()`)
**before the next stage loads**, so real peak VRAM at any instant is still
`max(single biggest stage) + CUDA context overhead` — not a sum. Whisper (~9–10GB) is
still likely the single biggest local stage even after YOLOv8-face's medium→xlarge
bump (~5–8GB estimated) — so the peak-VRAM figure probably doesn't move much, but the
margin between whisper and YOLOv8-face is now narrower than it was at medium, worth
double-checking once real numbers exist rather than assuming whisper stays on top.
Either way, comfortably inside a 24GB budget. Still needs confirming against **real
observed numbers** from a production run (each stage logs
`torch.cuda.memory_allocated()`/`memory_reserved()` right after its model loads — see
`src/utils/logging.py`'s `log_vram()` — check `data/logs/<job_id>/` after a real run
and reconcile against this doc). The first real vast.ai run (2026-07-25) predates all
of this (float16/vad_filter, medium/xlarge, Claude-API), so its logged VRAM numbers
reflect the *old* int8/nano/Qwen config — not directly comparable, wait for the next
run.

**Light-ASD was evaluated for a possible upgrade and deliberately kept unchanged**
(~1–2GB, same as before) — no bigger official checkpoint exists (it's designed to be
lightweight), and the natural "bigger" alternative, TalkNet-ASD, isn't established to
actually be more accurate: Light-ASD's own paper claims ~94% mAP on AVA-ActiveSpeaker
vs. TalkNet's officially reported ~90-92% mAP on the same benchmark. See `CLAUDE.md`'s
"Local-model quality pass" note for the full reasoning.

**ffmpeg rendering uses CPU `libx264`, not NVENC**, despite NVENC being lower VRAM and
faster — a deliberate choice to avoid introducing an unverified behavior change (ffmpeg
build flags, driver passthrough) right before a credit-constrained real test. Revisit
once the pipeline is proven end-to-end for real.

## GPU recommendation

**Decided (2026-07-26): RTX 3090/4090 24GB, not downsized.** Even at the re-corrected
~10–13GB peak (see above), a 12-16GB card would technically fit — but the user
explicitly chose to keep the 24GB tier rather than downsize, and redirect the VRAM
budget the local LLM no longer needs (moved to the Claude API) toward quality
upgrades on the stages that still run locally instead of toward cost savings:
whisper `int8`→`float16`+`vad_filter`, YOLOv8-face `nano`→`medium`→`xlarge`@`imgsz=1280`.
This reverses the cost-optimization framing this section carried before the Claude
API switch.

- **Chosen: RTX 4090/3090 24GB** — headroom is spent on transcription/detection
  precision (float16+VAD whisper, xlarge YOLOv8-face at higher inference resolution)
  rather than sitting idle as safety margin; also leaves room for further quality
  upgrades later (e.g. denser face-sampling fps, batch-size increases) without another
  hardware-tier decision.
- **Not chosen: 12–16GB** (e.g. RTX 3060 12GB, RTX 4070 12GB) — would technically fit
  the current ~10–13GB peak, but was ruled out in favor of spending the freed budget on
  quality rather than cost.
- **Avoid**: A100/H100 (massive overkill, priced out of budget), A5000/A6000 (no
  meaningful benefit for this workload, usually worse $/hr than 4090).

## CPU / RAM / Storage

- vCPU: 8–16 cores (ffmpeg decode/encode + preprocessing benefits from multi-core)
- RAM: 32GB minimum, 64GB comfortable (decode buffers for long 1080p video)
- Storage: 100GB minimum
  - Source video: ~3–8GB/hour (H.264) → 3hr video ≈ 10–25GB
  - Model weights: whisper large-v3 (~3GB checkpoint on disk regardless of
    int8/float16 — quantization affects VRAM at inference, not download size) +
    YOLOv8-face xlarge (still well under 200MB — 68.1M params, bigger than medium but
    nowhere near whisper/LLM-scale download sizes) + Light-ASD (<10MB, vendored in
    git) — whisper auto-downloads to the HuggingFace cache on first use, no manual
    step. **No local LLM checkpoint to download at all** since highlight detection
    moved to the Claude API on 2026-07-26 — this removes what was previously the
    single largest download (Qwen2.5-7B-Instruct, several GB) from the
    storage/bootstrap-time budget entirely.
  - Output clips: tens–hundreds of MB each

Filter vast.ai listings by GPU **and** vCPU/RAM together — host specs vary between
listings with the same GPU model.

## Billing strategy

- **On-demand instances, not interruptible/spot** — job shouldn't get preempted mid-render
- **Don't rent 24/7.** Boot the instance only when a video needs processing (manual trigger,
  or a script that boots the vast.ai instance via their API when a Whop webhook lands),
  destroy after the job completes
- **Persist model weights** via a vast.ai template/saved image or network volume to avoid
  re-downloading whisper's ~3GB checkpoint on every instance boot (no local LLM
  checkpoint to persist anymore — highlight detection is a Claude API call)

## Cost estimate

- RTX 4090 24GB + 8–16 vCPU/32–64GB RAM + 100GB disk: **~$0.35–0.55/hr** on the vast.ai marketplace
- Wall-clock per video (1–3hr source, 1080p): transcription a few minutes (float16 is
  slightly slower than int8 but still well under realtime), highlight detection is now
  a single Claude API call (seconds, not GPU-bound), detection/ASD/tracking ~10–20 min
  (sampling-rate dependent), rendering a few minutes → **~20–40 min total**, roughly
  unchanged from the pre-Claude-API estimate since the GPU-rental clock was never
  dominated by the LLM stage
- GPU-rental cost per video: **~$0.15–0.30** (unchanged — same GPU tier, similar
  wall-clock)
- **Plus Claude API cost per video: ~$0.20–0.31** (Sonnet-tier, unverified current
  pricing — check anthropic.com/pricing), for feeding the full transcript of a 1–3hr
  video through one highlight-detection call. This is a new line item that didn't
  exist under the local-LLM approach — see `CLAUDE.md` for the cost/architecture
  tradeoff analysis behind the switch (this was compared against, and found roughly
  comparable to, the local model's one-time checkpoint-download bottleneck plus its
  context-window limitation, not just raw $/video).
- **Total estimated cost per video: ~$0.35–0.61** (GPU rental + Claude API combined)

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

**The first real vast.ai run already happened (2026-07-25)** — full pipeline
(ingestion → processing → highlight detection → reframe) via `src.pipeline.run` on a
57-min video, but on the *old* config (whisper int8, YOLOv8-face nano, local
Qwen2.5-7B-Instruct) — its logged VRAM numbers reconcile against the old ~7–8GB
estimate, not this doc's current ~10–13GB one, and it surfaced the context-window bug
that motivated the Claude API switch (see `CLAUDE.md`). **A re-run with the current
code (float16+`vad_filter` whisper, xlarge YOLOv8-face @ `imgsz=1280`, Claude API,
captioning) is still needed** — every stage still logs timings and
`torch.cuda.memory_allocated()`/`memory_reserved()` right after its model loads
(`data/logs/<ingestion_job_id>/<stage>.log`); after that re-run, compare those real
numbers against this doc's re-corrected ~10–13GB peak-VRAM claim above and revise if
off, confirm YOLOv8-face's actual VRAM usage at xlarge (the ~5–8GB estimate is the
least-verified number in this doc — a rough scale-up, not a measurement), and check
the real per-video Claude API cost against the ~$0.20–0.31 estimate above.
