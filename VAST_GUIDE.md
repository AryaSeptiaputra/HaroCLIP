# HaroClip — vast.ai End-to-End Test Guide

Step-by-step for running the full pipeline (ingestion → download → highlight
detection → dynamic-crop reframe → caption burn-in) on a rented vast.ai GPU instance.
Written for a **single, credit-constrained run** — follow it in order rather than
improvising.

Branch to test: `vast-ai-e2e-prep` (not yet merged to `main` — merge only after this
test succeeds, so any fixes needed land in the same branch).

**Before renting: push your local branch.** `git clone`/`git checkout` on the
instance pulls whatever is on **GitHub**, not whatever's on your dev machine. If you
have local commits not yet pushed, the instance will run stale code — confirmed on a
real run (2026-07-27): the instance hit `ModuleNotFoundError: No module named
'transformers'` from a since-replaced code path, because 3 local commits (the
Claude-API switch, captioning, and the local-model quality pass) hadn't been pushed
yet. Fix: `git push origin vast-ai-e2e-prep` from your dev machine, confirm with
`git status -sb` that it shows `[origin/vast-ai-e2e-prep]` with no `ahead`/`behind`
count, *then* rent/clone. This costs nothing to check and can waste real credit if
skipped (diagnosing an error that was already fixed locally).

**Setup approach: install directly on the instance, no Docker.** A `Dockerfile` exists
in the repo for later reproducibility, but for this first real run it's simpler and
faster to pick a vast.ai template that already has PyTorch+CUDA installed and add our
remaining dependencies on top — avoids re-downloading a multi-GB CUDA base image and
torch wheel inside the instance, and avoids depending on vast.ai's nested-Docker
support (not guaranteed on every template).

**Recommended: paste `scripts/entrypoint.sh` into vast.ai's "On-start Script" field**
when configuring the instance/template. It automates everything in step 2 below
(clone/pull the repo, install deps, the two confirmed gotcha fixes, download the
YOLOv8-face weights, apply any pending DB migration) so that by the time you SSH in,
you can go straight to step 3. It's idempotent — safe on first boot, a restart, or a
stopped-then-started instance — and never contains a real secret value (it reads
`ANTHROPIC_API_KEY`/`HF_TOKEN` from vast.ai's own "Environment Variables" field on the
instance, set those there, not in the script). If you'd rather see each step happen
live over SSH the first time, skip this and follow step 2 manually instead — both
paths converge on the same result.

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
  an estimated ~10-13GB (whisper `float16` is still likely the single biggest local
  stage, with YOLOv8-face `xlarge` a distant second — see `docs/hardware-spec.md`) so
  a 12-16GB card would technically fit, but the 24GB tier is kept deliberately: the
  VRAM the local LLM no longer needs was redirected into quality upgrades (whisper
  `float16` + VAD filtering, YOLOv8-face `xlarge` at `imgsz=1280`) rather than
  downsizing for cost. Don't pick a smaller card to save money here — that would undo
  the deliberate quality tradeoff already made.
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

**If you configured `scripts/entrypoint.sh` as the On-start Script, this entire
section already ran automatically at boot** — check its output first
(`cat /var/log/onstart.log` on most vast.ai templates, or whatever your template
calls it) before repeating any of these steps manually. Everything below is both
the manual fallback and the reference for exactly what that script automates.

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

apt-get update && apt-get install -y ffmpeg   # most PyTorch templates don't include it
# No font apt-package needed anymore (removed 2026-08-01): captioning's
# burned-in text font (Rubik Bold) is committed to git under
# src/captioning/fonts/ and loaded directly by ffmpeg's subtitles filter
# `fontsdir` option — no OS-level font installation step at all.

pip install -r requirements.txt
# torch is already installed by the template — do NOT reinstall it, that's the
# multi-GB download this approach specifically avoids. Everything else (note:
# no transformers/accelerate/bitsandbytes needed anymore — highlight detection
# is a Claude API call, not a local model):
pip install faster-whisper ultralytics supervision python_speech_features

mkdir -p data/models
curl -L "https://github.com/lindevs/yolov8-face/releases/latest/download/yolov8x-face-lindevs.pt" \
    -o data/models/yolov8x-face-lindevs.pt
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

**Optional: steer highlight selection toward a campaign.** Add `--campaign-context
"<text>"` for a short inline brief, or `--campaign-file path/to/brief.txt` for a
longer one (mutually exclusive). This is layered on top of the hook-detection rules
as an additional filter, not a replacement — write the descriptive prompt yourself
(convert your campaign brief into it manually, nothing is parsed/uploaded here). It
persists on the ingestion job, so a `--job-id` resume doesn't need it repeated
unless you want to change it. **If your brief contains double quotes (brand names in
quotes, quoted phrases, etc.) or is more than a line or two, use `--campaign-file`
with a heredoc, not inline `--campaign-context "..."`** — confirmed on a real run
that embedded `"` characters break bash's argument parsing well before Python ever
sees it. See "Known gotchas" below for the exact fix.

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

**If the fix required a `git pull` mid-session** (e.g. you hit the stale-code gotcha
above, or any other code fix landed after you started), do these before re-running,
not just `git pull` alone:

```bash
git pull
pip install -r requirements.txt   # picks up any new/changed dependency (e.g. anthropic)
```

Also re-check anything that might have changed between commits:
- Env vars — re-`export` `ANTHROPIC_API_KEY`/`HF_TOKEN`/`LD_LIBRARY_PATH` if your SSH
  session dropped and reconnected (they don't persist across sessions, see step 2).
- Model weight filenames — if a code change bumped a default model variant (e.g.
  YOLOv8-face `medium`→`xlarge`), the old weight file won't match
  `YOLOV8_FACE_WEIGHTS_PATH`'s new default; re-run the relevant `curl` command from
  step 2 to fetch the new one rather than assuming what's already on disk is current.
- **DB schema changes — required, not optional, or the run will error.** This
  project has no migration system (`init_db()` is a bare
  `Base.metadata.create_all()`, which only creates missing tables, never adds
  columns to an existing one). `scripts/entrypoint.sh` now applies the known
  migrations automatically and idempotently on every boot, so if you're using it
  as your On-start Script, a restart handles this for you. If you're setting up
  manually instead, apply any pending migration by hand against your **existing**
  `data/haroclip.db` before resuming — e.g. the ones added so far:
  ```bash
  sqlite3 data/haroclip.db "ALTER TABLE highlight_jobs ADD COLUMN llm_response_path VARCHAR;"
  sqlite3 data/haroclip.db "ALTER TABLE ingestion_jobs ADD COLUMN campaign_context TEXT;"
  sqlite3 data/haroclip.db "ALTER TABLE highlight_clips ADD COLUMN segments_json TEXT;"
  sqlite3 data/haroclip.db "UPDATE highlight_clips SET segments_json = '[{\"start\": ' || start_seconds || ', \"end\": ' || end_seconds || '}]' WHERE segments_json IS NULL;"
  sqlite3 data/haroclip.db "ALTER TABLE caption_jobs RENAME COLUMN srt_path TO ass_path;"
  sqlite3 data/haroclip.db "ALTER TABLE processing_jobs ADD COLUMN description TEXT;"
  sqlite3 data/haroclip.db "ALTER TABLE processing_jobs ADD COLUMN upload_date VARCHAR;"
  sqlite3 data/haroclip.db "ALTER TABLE processing_jobs ADD COLUMN uploader VARCHAR;"
  sqlite3 data/haroclip.db "ALTER TABLE processing_jobs ADD COLUMN platform VARCHAR;"
  sqlite3 data/haroclip.db "ALTER TABLE ingestion_jobs ADD COLUMN hf_token TEXT;"
  ```
  The last four are for yt-dlp metadata capture (2026-08-01): `ProcessingJob` gained
  `description`/`upload_date`/`uploader`/`platform` columns, populated from the
  full-depth `info` dict `download_platform_video()` already fetches from yt-dlp at
  download time (no extra network call) — nullable, and always `NULL` for direct-URL
  (non-platform) jobs, which have no yt-dlp `info` dict at all. Distinct from
  `IngestionJob.raw_metadata`, a separate JSON blob captured earlier at ingestion
  validation time via a shallower `extract_flat` call.
  The middle two are for jump-cut clip support (2026-08-01): `HighlightClip` gained a
  `segments_json` column (a JSON list of `{start, end}` windows — more than one entry
  means a jump-cut clip stitched from non-contiguous moments) that rendering and
  captioning now read as the source of truth instead of the single `start_seconds`/
  `end_seconds` pair (those two columns still exist, now just the overall span, for
  display/logging). The `UPDATE` backfills any pre-existing rows as a single-segment
  list so old clips stay readable — **not optional**, `slice_words_to_clip` will crash
  on `NULL` `segments_json` during captioning otherwise.
  The last one is for the karaoke-caption rewrite (2026-08-01): captioning now burns a
  generated `.ass` file (word-by-word karaoke highlight) instead of a plain `.srt`, so
  `CaptionJob.srt_path` was renamed to `ass_path` — only relevant if `caption_jobs`
  already exists on this DB (i.e. captioning has run here before); a table that doesn't
  exist yet gets `ass_path` for free from `create_all()`.
  Skipping any of these makes the next write to that column fail with a `no such column`
  SQLite error — a fresh DB (no prior runs on this instance) needs nothing extra,
  `create_all` includes new columns from the start.
  The final one (2026-08-01) is for a per-job HF token: `IngestionJob` gained an
  `hf_token` column, settable via `--hf-token` on `pipeline.run`/`highlights.run` or
  from the frontend's ingestion form. When set, `run_highlight_detection`
  (`src/highlights/service.py`) exports it as the `HF_TOKEN` env var right before
  transcription — it takes over the process env var for that run, so it wins over
  (but doesn't require) the instance-level `export HF_TOKEN=...` from step 2 above.
  Deliberately excluded from the `IngestionJobRead` API response (unlike
  `campaign_context`) since it's a credential, not display data.

## 4. Using the browser UI on vast.ai (optional)

The CLI (`python3 -m src.pipeline.run`) is the primary, always-supported way to run
everything — this section is only for submitting a job through `frontend/`'s form
instead of typing flags. **The UI only covers job creation** (video link, optional
campaign brief PDF, optional Claude/HF API keys) — highlights/reframe/captioning are
still CLI-only, so step 5 below (switching back to the CLI to actually run the
pipeline) is not optional if you use the UI at all. Skip this whole section if typing
CLI flags is fine for you.

**1. Enable the setup.** Set `ENABLE_UI=1` in vast.ai's "Environment Variables" field
(same place as `ANTHROPIC_API_KEY`/`HF_TOKEN`) before starting/restarting the
instance — `scripts/entrypoint.sh` then installs Node.js 20.x and the frontend's
`npm` dependencies automatically on boot (skipped by default otherwise, since most
sessions never need Node.js at all). If the instance is already running and you don't
want to restart it, run the same block by hand instead:
```bash
export ENABLE_UI=1
bash scripts/entrypoint.sh
```

**2. Start both servers**, each in its own backgrounded shell (`Ctrl+Z` then `bg`, or
open a second Web Terminal tab for the second command):
```bash
python3 -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```
```bash
cd frontend && npm run dev
```
`python3`, not `python` — this image has no bare `python` on `PATH`. If `npm` itself
is missing, step 1's `ENABLE_UI=1` setup didn't run yet (check for the
`[entrypoint] ENABLE_UI=1: ...` log lines from `scripts/entrypoint.sh`).

**3. Open an SSH tunnel — from a brand-new terminal window on your own local
machine, not from the Web Terminal/SSH session you're already using to control the
instance.** This is the single most common mix-up: running the `ssh` command below
*inside* the instance just makes the instance try to SSH somewhere else and fails
with "Network is unreachable" or similar. Get the **real** `ssh -p <port> root@<host>`
command from vast.ai's instance "Connect" button (the values below are placeholders —
don't paste them verbatim), then add the two `-L` forwards to that real command:
```bash
ssh -p <port> -L 5173:localhost:5173 -L 8000:localhost:8000 root@<host>
```
This opens a **second, separate** SSH session just for the tunnel — leave it running
in that window for as long as you want the UI reachable. It requires an SSH key
registered under vast.ai's **Account → SSH Keys** settings; if your only access so far
has been vast.ai's Web Terminal (no key needed there) or an SSH session that was
already open for you, generate a key locally and register it first:
```bash
ssh-keygen -t ed25519
cat ~/.ssh/id_ed25519.pub   # paste this into vast.ai → Account → SSH Keys
```
A key added to an already-*running* instance may not take effect until the instance
restarts — if you get "Permission denied (publickey)" right after adding a key, try
restarting the instance before troubleshooting further.

**4. Open `http://localhost:5173`** in your local browser (through the tunnel from
step 3). The backend's CORS is locked to exactly this origin (`src/api/main.py`),
which is why the tunnel forwards to `localhost` on both ends rather than the
instance's public IP — a direct `http://<instance-ip>:5173` won't work. Fill in the
video link, and optionally expand "Advanced options" for a campaign brief PDF and/or
Claude/HF API keys, then submit.

**5. Get the job's ID and run the actual pipeline via CLI.** The UI doesn't show the
raw job ID anywhere in its current form — look it up on the instance instead:
```bash
sqlite3 data/haroclip.db "SELECT id, source_url, status FROM ingestion_jobs ORDER BY created_at DESC LIMIT 5;"
```
Wait until `status` reaches `ready` (the UI's job list also reflects this), then run
the pipeline against that job id — this is the same `export ANTHROPIC_API_KEY=...`
requirement as the pure-CLI flow in step 2 of this guide, so make sure that's still
set in whichever shell you run this from:
```bash
python3 -m src.pipeline.run --job-id <id-from-the-query-above>
```
Submitting through the UI does **not** start any processing by itself — it only gets
the video downloaded/validated and (optionally) the campaign brief summarized.

**6. When you're done with the UI, stop both background servers** — they don't need
to stay running while the CLI pipeline (step 5) does the actual GPU work, and leaving
them up is a small amount of unnecessary resource use on a billed instance:
```bash
pkill -f uvicorn
pkill -f vite
```
(Or `fuser -k 8000/tcp` / `fuser -k 5173/tcp` if `pkill` doesn't find them — e.g. you
switched shells since starting them and `jobs -l`'s job table doesn't carry over.)

There's no cost difference between creating a job via the UI vs. `--url` on the CLI —
either way, the actual heavy work (transcription, detection, rendering) only happens
when step 5's `pipeline.run --job-id` (or `--url` directly) is invoked.

## 5. Collect results before destroying the instance

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
- `data/captioned/<ingestion_job_id>/*.mp4` — **the final deliverable**: reframed
  clips with captions burned in. Nested per ingestion job (2026-08-01) so results
  from different videos never mix in the same folder.
- `data/reframed/<ingestion_job_id>/*.mp4` — the dynamic vertical-crop clips *before*
  captioning (useful to compare against `data/captioned/` if caption placement/timing
  looks off). Also nested per ingestion job.
- `data/captions/<ingestion_job_id>/*.ass` — the generated karaoke subtitle files
  (not `.srt` — captioning switched to `.ass` in the 2026-08-01 karaoke-caption
  rewrite), one per clip, also nested per ingestion job — open these directly if you
  want to check caption text/timing without opening the burned-in video.
- `data/clips/<ingestion_job_id>/` — the intermediate static clips (before reframing).
- `data/haroclip.db` — SQLite DB with full job status/metadata if useful.

```bash
# from your local machine, not the instance:
scp -r -P <port> root@<instance-ip>:~/HaroCLIP/data ./haroclip-results
```

(Adjust the path/port to whatever vast.ai's SSH connection details show for your
instance.) Then **destroy the instance** — don't leave it running once you have what
you need.

## 6. What to check in the logs afterward

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
- **`run.py: error: unrecognized arguments: ...` from `--campaign-context`** — a shell
  quoting issue, not a bug in the script. If your campaign brief contains double
  quotes (e.g. `kata "BOXABL"`), wrapping the whole `--campaign-context` value in
  double quotes breaks: bash closes the argument at the *first* embedded `"`, and
  every bare word after that becomes its own unrecognized argument. Two fixes:
  - **Preferred for any brief with quotes or that's more than a line or two**: use
    `--campaign-file` instead, writing the brief with a quoted heredoc so bash does
    zero interpretation of its contents:
    ```bash
    cat > campaign_brief.txt << 'EOF'
    Your brief here, with "quotes", (parentheses), and anything else — verbatim.
    EOF
    python3 -m src.pipeline.run --url "..." --campaign-file campaign_brief.txt
    ```
    (The `'EOF'` delimiter — quoted — is what disables interpretation; a bare `EOF`
    would still expand `$variables` and backticks inside the heredoc.)
  - **Quick fix for a short inline brief**: wrap `--campaign-context` in **single**
    quotes instead of double quotes (works as long as the brief itself contains no
    single quotes/apostrophes): `--campaign-context 'kata "BOXABL", "Casita"'`.

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
