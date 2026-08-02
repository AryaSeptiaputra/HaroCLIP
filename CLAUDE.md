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
  **30-60s**, spread across the *entire* video rather than clustered in one section,
  natural sentence boundaries, self-contained), **optionally layered with
  `IngestionJob.campaign_context`** (`build_system_prompt()` — an additional filter
  appended to the base system prompt, not a replacement: still has to be a genuine
  hook first, campaign relevance breaks ties rather than overriding the hook bar) to
  get up to 15 ranked candidate segments as
  strict JSON (`parse_candidates` validates timestamps/duration, drops malformed
  entries rather than failing the whole job), then renders each as a clip via ffmpeg
  (`src/rendering/clipper.py`, `-ss`/`-t` as *input* options so re-encoding stays
  frame-accurate without the `-ss`+`-to` absolute-timeline gotcha) — **each candidate
  may be one continuous span or, since 2026-08-01, a "jump-cut" of up to 3
  non-contiguous segments stitched together when they serve the same single point**,
  see "Highlight clips can now be 'jump-cuts'" further down for the full story.
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
  vertically), then renders it (`src/reframe/renderer.py`) by cropping/resizing each
  frame with OpenCV and piping the raw result into a single ffmpeg subprocess that
  encodes straight to libx264 (`-crf 17`) and stream-copies in the original audio from
  a second input — see "Reframe's crop encode switched off a lossy intermediate codec"
  below for why this isn't a plain OpenCV `VideoWriter` anymore. Tracked in
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
  first) and written as a hand-formatted `.ass` (`write_ass` — no subtitle library
  dependency added; see "Captions became word-by-word karaoke" below for why `.ass`
  replaced the original plain `.srt`). Burn-in is a **separate ffmpeg pass after**
  reframe's own render, not folded into it: captioning re-encodes
  (`-vf "subtitles='<ass path>'" -c:v libx264 -crf 18 -c:a copy`) from the
  already-final reframed video into `data/captioned/<highlight_clip_id>.mp4` — the
  true final deliverable. Tracked in `caption_jobs`, keyed by `highlight_clip_id` (same
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
  whatever fontconfig finds by default, uncontrolled. Also added `-crf 18` to the
  libx264 re-encode (default CRF 23 under-serves compact high-contrast text
  glyphs) — this was, at the time, the one ffmpeg call in the project with an
  explicit CRF; as of 2026-08-01 `src/reframe/renderer.py`'s crop encode also has
  one (`-crf 17`), for a different reason — captioning's 18 is a final-deliverable
  quality target, reframe's 17 is intermediate headroom against the lossy
  re-encode stacked on top of it, not the deliverable quality itself. (The
  `original_size=1080x1920` filter option mentioned in earlier versions of this
  doc no longer applies — the karaoke rewrite below moved to a real `.ass` file,
  whose own `PlayResX`/`PlayResY` supersede it.)
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

**Highlight clips can now be "jump-cuts" — stitched from up to 3 non-contiguous
source-video segments (2026-08-01).** Previously a `HighlightClip` was always exactly
one continuous `[start, end]` window. `HighlightClip.segments_json` (new column, JSON
list of `{"start", "end"}` dicts — the source of truth for rendering/captioning now;
`start_seconds`/`end_seconds` still exist but are just the overall span, kept for
display/logging) lets a single clip stitch together multiple separated moments from
the source video, hard-cut together (no crossfade). This is **always available to
Claude as an option, not a flag** — `src/highlights/prompt.py`'s "Jump-cuts" rules
instruct it to default to one continuous segment and only reach for a jump-cut when
both hold: (a) there's a meaningfully-sized chunk between the kept segments that's
genuinely not needed for the point (filler/tangent/repetition, not a few seconds of
"um"), and (b) the stitched result is convincingly better than the best available
continuous window for the same idea. **Critically, all segments in one clip must still
serve the SAME single idea/point** — a jump-cut is for cutting dead weight out of one
story, never for splicing separate hooks together to hit the duration bar; the prompt
explicitly forbids that and requires the `reason` field to name what was skipped and
why, both to force the model to justify the cut and to give a manual spot-check point
during real-run verification (this coherence rule can't be validated in code, only
instructed and spot-checked). Constraints enforced in `parse_candidates`
(`src/highlights/llm.py`): `MAX_SEGMENTS_PER_CLIP = 3`, `MIN_SEGMENT_SECONDS = 8`
(conservative — segments stay long enough to feel natural, not like a random
compilation), segments must be chronological/non-overlapping, and the **total**
duration across all segments (not any single segment) is what's checked against the
existing `MIN_CLIP_SECONDS`/`MAX_CLIP_SECONDS` (30-60s) bounds. `render_clip`
(`src/rendering/clipper.py`) now takes a list of segments instead of one `start`/`end`
pair — one ffmpeg call per clip, one `-ss`/`-t`-seeked input per segment (keeps the
fast-seek behavior even for a segment deep into a long source video) feeding a
`trim`/`atrim` + `concat` filter graph; this also covers the plain single-segment case
(`N=1`) with no special-casing, so non-jump-cut clips render through the same code
path as before. `slice_words_to_clip` (`src/captioning/subtitles.py`) — previously a
flat `word.start - start_seconds` shift, only valid for one window — is rewritten to
walk the clip's segments in order, dropping words that fall in the gap between
segments, clamping words that straddle a segment boundary, and shifting by each
segment's own start plus the cumulative duration of segments already placed, so
caption timing stays correct across a jump-cut's boundary. Reframe (`src/reframe/`)
needed **no changes** — it operates entirely on the already-rendered clip file via
local frame index, never re-seeking into the source video by timestamp. Existing
`data/haroclip.db` files need a one-time manual migration (see `VAST_GUIDE.md`):
`ALTER TABLE highlight_clips ADD COLUMN segments_json TEXT;` plus a backfill of
existing rows to a single-segment list derived from their `start_seconds`/
`end_seconds` — `scripts/entrypoint.sh` applies both automatically and idempotently.
**Verified locally**: `render_clip` against a synthetic multi-segment source (output
duration matches the sum of segment durations, both for a single segment and a
3-segment jump-cut), `parse_candidates` against hand-written valid/invalid multi-segment
JSON (too many segments, segment under the minimum, overlapping/non-chronological
segments, total duration out of bounds, mixed valid+invalid lists), and
`slice_words_to_clip` against a hand-written transcript (gap words correctly dropped,
boundary words correctly clamped, cumulative offset correct). **Not yet verified**:
whether Claude's actual jump-cut judgment in practice respects the "same idea only"
rule on a real video — needs a real vast.ai run, same caveat as highlight-detection
quality generally.

**Captions became word-by-word karaoke, with numeral emphasis and a safe-zone
margin fix; reframe's crop encode switched off a lossy intermediate codec
(2026-08-01).** Prompted by research into short-form-video communication science
(word-by-word highlighted captions are cited at a 12-25% watch-time lift over static
captions — the "eye-lock" effect of each word visually popping as it's spoken) plus a
real quality bug found while investigating: `src/reframe/renderer.py` was encoding its
per-frame crop/resize output with OpenCV's `cv2.VideoWriter(fourcc="mp4v")` — a weak
codec — before captioning re-encoded it *again* with libx264, stacking a lossy
intermediate ahead of the quality-defining final pass.

- **Karaoke captions** (`src/captioning/subtitles.py`): burn-in switched from a plain
  `.srt` to a generated `.ass` file using per-word ASS `\k` karaoke tags. Each burst
  cue (still the existing ≤4-word/≤1.5s grouping from `build_burst_cues`, now enriched
  with `CaptionCue.words`) starts fully white (`SecondaryColour=&H00FFFFFF`) and each
  word turns yellow (`PrimaryColour=&H0000FFFF`) and *stays* yellow as the cue plays,
  timed to the gap until the next word's actual onset (not the word's own
  start/end) so the color change lands exactly when the next word is spoken. **Real
  local libass verification** (this dev machine's ffmpeg has `libass`/`ass`/`subtitles`
  filters compiled in, confirmed via `ffmpeg -filters`): rendered the generated `.ass`
  onto a synthetic clip and visually inspected extracted frames — the
  PrimaryColour-is-"sung"/SecondaryColour-is-"unsung" ASS convention rendered exactly
  as expected on the first try, **no color-slot swap needed** (the plan going in
  explicitly flagged this as needing real-render confirmation rather than trusting the
  spec, same practice as the FontSize-36 story above).
- **Numeral emphasis**: any word containing a digit gets a static cyan accent
  (`NUMBER_ACCENT_COLOR = &H00FFFF00`, deliberately a different hue from the karaoke
  yellow) via an inline `\1c\2c` override + trailing `\r` reset, so it reads as
  accented whether or not it's the currently-"spoken" word — reinforcing this
  project's existing hook philosophy that concrete numbers/specifics are part of what
  makes a moment a genuine "concrete insight" hook (`src/highlights/prompt.py`).
  Numbers-only for now (objective, no maintenance burden); `EMPHASIS_KEYWORDS` exists
  as an empty `frozenset` for an easy future curated-word-list extension. Confirmed via
  the same local render: a numeral stays cyan through its own cue and *after*, even
  once a later word becomes the active spoken one.
- **Safe-zone margin, narrowed to TikTok + YouTube Shorts specifically
  (updated 2026-08-01)**: the `.ass` style's `MarginV`/`MarginR`/`MarginL` were
  `260`/`60`/`60` (a rough "~13.5% of frame height" estimate framed generically as
  "TikTok/Reels/Shorts"). Per user direction, Instagram Reels was dropped from the
  target platform set and the margins were replaced with real researched per-app
  safe-zone figures: `MarginV=480`, `MarginR=140`, `MarginL=60`. Since one burned-in
  output has to clear both remaining platforms, each margin takes the **stricter**
  of the two: TikTok's published safe-zone guides cite a ~484px bottom margin
  (caption bar/sound attribution/username/overlays — rounded to 480) and ~140px
  right margin (profile/like/comment/share/bookmark icon stack), both larger than
  YouTube Shorts' figures (~300-400px bottom, ~96-120px right), so TikTok's numbers
  win on both edges; left stays at 60px since neither app has a UI element there.
  A side effect worth noting: with `Alignment=2` (bottom-center), libass centers
  text within `[MarginL, PlayResX-MarginR]`, so the asymmetric 60/140 box biases
  the caption slightly left of true frame-center — deliberate, not a bug, since it
  nudges the caption away from the right-side icon column present on both apps.
  These figures come from third-party creator-tool safe-zone guides (neither
  platform publishes official specs) — confirmed locally only against the raw
  frame edge, **not yet confirmed against the real apps' actual UI** (that still
  needs a phone-side check, not just local rendering — the research narrows which
  numbers to target, it doesn't replace that check).
- **`src/captioning/service.py` simplified**: `SUBTITLE_STYLE`/`force_style`/
  `original_size` are gone — style now lives entirely in `subtitles.py`'s
  `ASS_TEMPLATE` `[V4+ Styles]` section, which the `.ass` file self-describes
  (including its own `PlayResX`/`PlayResY`), so the ffmpeg `-vf` argument shrank to
  just `subtitles='<path>'`.
- **`CaptionJob.srt_path` renamed to `ass_path`** — a column rename on an existing
  table, so it needs the project's usual manual-migration treatment: `scripts/
  entrypoint.sh` now runs an idempotent `ALTER TABLE caption_jobs RENAME COLUMN
  srt_path TO ass_path;` (only fires if the table exists and still has the old
  column), same pattern documented in `VAST_GUIDE.md`'s migration list. A fresh
  `caption_jobs` table gets `ass_path` for free from `create_all()`.
- **Reframe's crop encode switched off a lossy intermediate codec**
  (`src/reframe/renderer.py`): the `cv2.VideoWriter(fourcc="mp4v")` intermediate file
  plus its separate stream-copy remux subprocess are gone, replaced by a single ffmpeg
  subprocess — raw cropped/resized frames piped over stdin as one input, the original
  clip as a second input for audio (stream-copied), video encoded straight to libx264
  (`CROP_CRF = "17"`). 17 rather than the ffmpeg default 23 *or* captioning's
  final-pass 18: this is still an intermediate that captioning re-encodes again, but
  two stacked lossy re-encodes compound visible loss more than one
  clearly-above-final-quality intermediate plus one final pass does — file size
  doesn't matter here since captioning immediately re-encodes it anyway.
  `FFMPEG_TIMEOUT_SECONDS` bumped `120`→`600` (same reasoning as `clipper.py`/
  captioning's 600s budgets — this is now a real CPU-bound encode, not a stream copy).
  One implementation subtlety: `proc.stderr` is drained on a background thread
  *concurrently* with writing frames to `proc.stdin` — ffmpeg's own log/progress
  output can fill the OS pipe buffer during a longer encode, and without draining it
  while blocked on stdin writes, both sides can deadlock. **Verified locally**: a
  synthetic 10-second 1920×1080 source clip rendered through the new path in ~4s,
  producing a correctly-shaped (1080×1920, h264+aac, matching 10s duration) output
  with no mp4v-typical blockiness on inspection, and the stderr-drain thread didn't
  deadlock at that length (long enough to genuinely exercise concurrent
  stdin-write/stderr-read, unlike a 2-3s clip).
- **Full regression verified locally**: `run_captioning` run end-to-end against a
  fake `HighlightJob`/`HighlightClip`/`ReframeJob` fixture (real transcript slicing,
  real `.ass` generation, real libass burn-in against the real new reframe-pipeline
  output) reached `READY`, with `job.ass_path`/`job.output_path` set correctly and a
  word past the clip's end window correctly absent from the generated `.ass`. The
  `srt_path`→`ass_path` `RENAME COLUMN` migration was tested against a throwaway
  pre-existing `caption_jobs` row (data preserved) and confirmed idempotent (a second
  run is a no-op).
- **Still unverified, pending a real vast.ai run** (same caveat category as the rest
  of this module): karaoke timing/color against *real* speech cadence rather than
  hand-written fake word timestamps; whether `MarginV=480`/`MarginR=140` (see "Safe-zone
  margin, narrowed to TikTok + YouTube Shorts" above) actually clear the real TikTok/
  YouTube Shorts apps' UI (not just the raw frame edge); real encode-time/file-size
  cost of `CROP_CRF=17` at production clip lengths on the rented GPU instance's CPU.

**Final output filename now title-based, resolution/framerate forced explicitly
(2026-08-01), per user direction.** Previously the deliverable was
`data/captioned/<highlight_clip_id>.mp4` — a raw UUID meaningless to a human browsing
the output folder — and no stage in the pipeline set an explicit output framerate
anywhere (output fps was always just whatever the upstream source/reframe stage
happened to produce).
- `src/captioning/storage.py::captioned_output_path()` now takes `(video_title, rank)`
  instead of `highlight_clip_id`, producing `<title-slug>_captioned_<rank:02d>.mp4`
  (e.g. `my_awesome_video_part_1_captioned_01.mp4`) via a new `slugify()` helper
  (lowercase, non-alphanumeric runs collapsed to `_`, truncated to 80 chars).
  `rank` reuses the existing `HighlightClip.rank` concept already used elsewhere as
  `clip_01.mp4`. Falls back to the slug `"video"` when the source `IngestionJob` has
  no title (e.g. a `DIRECT`-link job, which never gets a yt-dlp title). `run_captioning`
  (`src/captioning/service.py`) now does one extra lookup —
  `db.get(IngestionJob, ingestion_job_id)` — to get the title; no schema/migration
  needed, `CaptionJob.output_path` is just a string column.
  **Formerly a known accepted limitation, now resolved (2026-08-01, see "Per-video
  output folders" below)**: two ingestion jobs sharing an identical video title used
  to collide in the flat `data/captioned/` directory (rank alone doesn't disambiguate
  across videos). Closed by nesting every render under a `data/captioned/<ingestion_job_id>/`
  subfolder instead of changing the filename itself — the title-slug filename stays
  exactly as described above, just no longer needs to be globally unique.
- The final captioning ffmpeg burn-in pass now explicitly forces `-s 1080x1920 -r 120`
  on its output, regardless of what the upstream reframe stage produced (belt-and-
  suspenders on top of `OUTPUT_WIDTH`/`OUTPUT_HEIGHT` already being 1080x1920 in
  `src/reframe/renderer.py` — no change needed there). `-r 120` is plain CFR frame
  duplication (ffmpeg's default output-side `-r` behavior), not true motion
  interpolation (`minterpolate`) — source footage is never actually shot at 120fps,
  this simply hits the requested delivery framerate cheaply, per explicit user choice
  over the far more CPU-expensive interpolation alternative.
- **Verified locally**: a synthetic fixture (`IngestionJob` with a real-looking
  punctuated title, `HighlightJob`/`HighlightClip`(`rank=1`)/`ReframeJob` reaching
  `READY`) run through the real `run_captioning()` end-to-end reached `READY`,
  produced the exact expected filename
  (`my_awesome_video_part_1_2026_captioned_01.mp4`), and `ffprobe` on the produced
  file confirmed `1080x1920` at `120/1` fps. `slugify()` also hand-checked against
  `None`, empty string, and a 200-character title (correctly truncated to 80 chars).
  **Not yet verified**: a real YouTube-titled video end-to-end on vast.ai — same
  caveat as the rest of this module, covered by the already-planned real vast.ai
  re-run below.

**Custom per-frame caption renderer (Pillow) — researched, shelved, not
implemented (2026-08-01).** While narrowing the safe-zone work above, the
previously-deferred question of a Pillow/OpenCV-based custom caption renderer (as
an alternative to the ASS/libass approach shipped this session) was revisited as a
design-only discussion, per user direction — no code was written for it. Findings:
- **ASS/libass already covers the most common ask (in/out animation) natively**:
  `\fad(t1,t2)` (fade), `\t` transforms combined with `\fscx`/`\fscy` (scale
  pop-in/out), and `\move` (slide) are all standard ASS tags — pure additions to a
  `Dialogue` line, no new dependency, no new rendering pass. `ASS_TEMPLATE`
  (`src/captioning/subtitles.py`) doesn't use any of these yet, so they remain a
  cheap future extension within the existing approach if simple in/out animation is
  ever wanted.
- **A literal hybrid (ASS renders the base text, Pillow renders a separate effects
  layer, composited via ffmpeg's `overlay` filter) is technically possible but
  costly**: it requires two independent renders — a libass burn-in pass and a
  separate Pillow-rendered alpha video — muxed together, which reintroduces a
  stacked lossy re-encode pass, directly working against the mp4v-intermediate fix
  just made to `src/reframe/renderer.py` this same session. Not a clean split.
  If Pillow is ever genuinely needed for a clip (something no ASS tag combination
  can express — e.g. content-aware positioning driven by `src/reframe/`'s face-track
  data, or custom emoji glyphs), the recommendation on record is to render that
  clip's *entire* caption text through Pillow rather than mixing ASS and Pillow
  output for the same file.
- **Cost if ever pursued**: new `Pillow` dependency (not in `requirements.txt`
  today — no other module needs it); a new per-frame text-compositing pipeline
  reusing the raw-frame-over-stdin-to-ffmpeg pattern from `src/reframe/renderer.py`,
  but paying real per-frame Python compositing cost across 1080×1920 for a 30-60s
  clip (unmeasured, and meaningfully more expensive than libass's one-shot C
  rendering); loses the "no new pip dependency, libass is already a required system
  prerequisite" simplicity that keeps the shipped ASS approach low-risk.
- **Decision: shelved, not an open "revisit if X" item.** Per explicit user
  instruction ("kita tanggalkan saja penggunaan pillow ini"), this isn't framed as
  a standing recommendation to reconsider once some condition is met — it's
  recorded here as considered-and-closed, the same way the Light-ASD-vs-TalkNet
  evaluation above is. Reopen only on a future explicit user request, not on the
  assistant's own judgment that a trigger condition has been reached.

**Highlight clip cut-in/cut-out precision fixed, duration cap loosened 60→75s
(2026-08-01).** Prompted by real user-observed clips cutting in or out mid-sentence,
sometimes ruining caption timing. Root cause traced to two independent gaps, neither
previously caught since this stage has no real-footage verification yet (see
highlights-module verification caveat below):

- **Timestamp precision gap**: `format_transcript()` (`src/highlights/prompt.py`)
  showed Claude segment timestamps truncated to whole seconds (`int(seg.start)`) via
  `mm:ss` formatting, while the output contract asked Claude to return float-precision
  cuts — so Claude's returned decimals were effectively guesses, not real word/sentence
  edges. Meanwhile `parse_candidates` (`src/highlights/llm.py`) never cross-checked
  those timestamps against the word-level `TranscriptWord` data already produced by
  transcription (used by captioning, but previously unused in the highlight-detection
  path) — whatever float Claude returned was used as-is for both the ffmpeg cut
  (`render_clip`) and caption word-slicing (`slice_words_to_clip`), which explains both
  symptoms: a cut landing after a word's true onset produces a mid-word video cut, and
  landing after a word's `.end` makes `slice_words_to_clip` drop that word from
  captions entirely (`word.end <= seg_start` → skipped, not clamped).
- **Duration gate had no accommodation for long ideas**: `parse_candidates` rejected
  (silently dropped, no trim/extend) any candidate whose total duration fell outside
  the hard `[30, 60]` window — an idea that genuinely needed more than 60s either got
  forced into an unnatural mid-thought truncation by the LLM, or its candidate was
  dropped.

**Fix, split into the two pieces above (user-decided direction — precision fixed via
both prompt improvement AND code-side snapping, not either alone; duration capped
raised rather than kept strict):**
- `format_transcript()` now shows 1-decimal-second precision via a new
  `_format_timestamp()` helper (deciseconds-first divmod, avoids a carry bug where a
  naive round of e.g. `59.96` would render the invalid `"01:60.0"` instead of
  `"02:00.0"`). The system prompt (`src/highlights/prompt.py`) was reworded to tell
  Claude explicitly that its start/end are estimates which get snapped to a real word
  boundary afterward — it should still aim for accuracy (smaller correction), but
  doesn't need word-perfect precision, resolving the prior tension between "use only
  segment timestamps as cut points" and "start/end on natural sentence boundaries"
  (sentence boundaries often fall mid-segment).
- **New `snap_candidates()` in `src/highlights/llm.py`**, called from
  `src/highlights/service.py` right after `parse_candidates` and before persistence/
  rendering: flattens all `TranscriptWord`s across the transcript, and for each
  candidate segment snaps `start` to the nearest word **start** and `end` to the
  nearest word **end** within a `SNAP_WINDOW_SECONDS = 2.0` search radius (deliberately
  asymmetric — start never snaps to a word's end or vice versa — so the word at a cut
  point is always either fully included or fully excluded, never split in half). Falls
  back to Claude's raw value (with a `logger.warning`, not a rejection) if no word
  falls within the window — treated as a possibly-legitimate situation (e.g. a clip
  starting right as speech resumes after silence) worth a manual spot-check flag,
  not an automatic failure. After snapping, each candidate is re-validated with the
  exact same checks `parse_candidates` already does (range, `MIN_SEGMENT_SECONDS`,
  chronological/non-overlap, total duration) since snapping can shift a segment enough
  to fail one of them — a candidate that fails re-validation is dropped (logged), never
  raised mid-loop; `HighlightError` only raises if the whole result ends up empty, same
  contract as `parse_candidates`. Works per-segment independently for jump-cut clips
  (up to 3 non-contiguous segments), searching the full flattened word list each time
  rather than scoping to "its own" transcript segment.
- **`MAX_CLIP_SECONDS` raised from 60 to 75** (`src/highlights/llm.py`) — 60 remains the
  default/target ceiling per the prompt's duration rule, 75 is a deliberate stretch
  allowance specifically so a genuinely strong idea isn't forced into a mid-thought
  truncation just to hit 60s. Prompt rules were strengthened to make "never end
  mid-sentence/mid-thought" unconditional: if an idea still doesn't fit in 75s even
  after trimming filler via a jump-cut, the candidate must be skipped entirely rather
  than truncated — added as a 4th bullet to the jump-cut "Decision order" list.
  `MIN_CLIP_SECONDS` (30), `MIN_SEGMENT_SECONDS` (8), and `MAX_SEGMENTS_PER_CLIP` (3)
  are unchanged — out of scope for this fix.
- **No changes to `src/rendering/clipper.py`, `src/captioning/subtitles.py`,
  `src/reframe/`, or `HighlightClip`'s schema** — the fix happens entirely upstream of
  these; snapped floats flow through the exact same plumbing (`segments_json`, etc.)
  that already existed, with no shape change and no migration needed. One incidental
  cleanup bundled into the same edit: `service.py`'s per-clip render loop reused the
  variable name `segments` (candidate's clip segments) shadowing the outer transcript
  `segments` variable — harmless before this change (nothing after read the outer one
  again), but risky now that `snap_candidates` is a second consumer of the transcript
  `segments` right before the loop; renamed the loop variable to `clip_segments`
  (matches `slice_words_to_clip`'s own parameter name for the same concept).

**Verified locally** (no local CUDA GPU — same ad hoc hand-written-fixture pattern as
the rest of this module's verification): `_snap_edge` against a hand-built
`TranscriptWord` fixture confirms start snaps only to word starts and end only to word
ends (not the opposite edge) even when a raw value sits closer to an adjacent word's
opposite-type boundary; a jump-cut candidate's two segments snap independently against
the full word list; a raw value with no word within the window falls back to the raw
value with a warning logged; a candidate that becomes invalid after snapping (segment
shrinks below `MIN_SEGMENT_SECONDS`) is dropped while a separate valid candidate in the
same batch survives; a 68s single-segment candidate is now accepted by
`parse_candidates` (previously rejected under the old 60s cap) while an 80s candidate
is still rejected under the new 75s cap; `_format_timestamp`/`format_transcript`
produce correct decimal output including the `59.96` carry-into-next-minute edge case;
existing `parse_candidates` rejection paths (too-short segment, mixed valid/invalid
candidates) re-checked to confirm the `MAX_CLIP_SECONDS` bump didn't affect them.
**Not yet verified**: actual snap behavior against a real transcript's real
word-boundary timing noise, and whether Claude's cut choices actually improve in
practice now that it sees decimal-precision timestamps and knows about the snap
safety net — both need a real vast.ai run, same caveat as highlight-detection quality
generally (see below).

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
instance's ffmpeg build actually has `libass` compiled in. **Update (2026-08-01):**
the burn-in mechanism changed (karaoke `.ass` instead of plain `.srt`, see "Captions
became word-by-word karaoke" above), and unlike most of this project's caption work,
the karaoke color-slot mapping and the numeral-emphasis override *were* confirmed
against a real local libass render this time (this dev machine's ffmpeg does have
`libass` compiled in) — that specific risk is retired. The underlying "real speech
cadence" and "real TikTok-app safe-zone clearance" caveats are unchanged and still
open, since a hand-written fake transcript and a raw solid-color test frame are still
not real speech or a real phone screen.

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

**Platform video metadata now captured during processing (2026-08-01).**
`src/processing/downloader.py::download_platform_video()` already called
`yt_dlp.YoutubeDL(opts).extract_info(url, download=True)` and only used the returned
`info` dict for `prepare_filename(info)` — the rest (description, upload date,
uploader, platform) was discarded. It now also returns a `PlatformVideoMetadata`
(`src/processing/metadata.py::extract_platform_metadata()`, a pure function over the
`info` dict — no extra network call, the dict was already being fetched), and
`run_processing()` (`src/processing/service.py`) persists it onto four new nullable
`ProcessingJob` columns: `description`, `upload_date` (raw yt-dlp `"YYYYMMDD"` string,
not parsed to a date), `uploader`, `platform` (yt-dlp's `extractor_key`, e.g.
`"Youtube"`/`"TikTok"`). Always `NULL` for `DIRECT`-link jobs (plain `httpx` streaming,
no yt-dlp `info` dict ever exists on that path) — `download_direct_video()` itself is
unchanged. Deliberately distinct from `IngestionJob.raw_metadata` (an earlier,
possibly-shallower JSON blob captured at ingestion *validation* time via
`extract_flat: "in_playlist"`) — this is the full-depth `info` dict from the actual
download call. Existing `data/haroclip.db` files need the usual one-time manual
migration (`scripts/entrypoint.sh` applies it automatically and idempotently; see
`VAST_GUIDE.md`'s migration list for the exact `ALTER TABLE` statements).
**Verification status**: `extract_platform_metadata()` is a pure function, easily
unit-tested against a hand-built dict fixture — no real-content verification caveat
applies to the mapping logic itself. Whether yt-dlp actually populates these fields
reliably across the real range of target platforms is unverified locally (needs a
real vast.ai run against real platform URLs).

**Campaign brief PDF upload + Claude summarization (2026-08-01).** The
"campaign context as a prompt" design (2026-07-29, see above) required the user to
manually convert their brief into descriptive text themselves. That manual step is
now optional: a campaign brief PDF can be uploaded directly and summarized into
`IngestionJob.campaign_context` by Claude — closing this loop without resurrecting
the removed `src/campaign/` module (per its own explicit instruction to build fresh
rather than resurrect). New module `src/campaign_brief/` (deliberately a different
name from the deleted `src/campaign/`):
- `summarizer.py::summarize_campaign_brief(pdf_bytes)` sends the PDF directly to
  Claude as a native `document` content block (base64, no beta header required) —
  Claude reads both text and visual elements (charts/tables) in the brief natively,
  so **no PDF-parsing library was added** (this repo had none before). Model:
  `claude-haiku-4-5` (env override `CAMPAIGN_BRIEF_LLM_MODEL`, mirroring
  `HIGHLIGHT_LLM_MODEL`'s pattern but kept as a separate variable — the two calls
  have unrelated cost/quality requirements and shouldn't be forced to move together).
  The system prompt explicitly instructs Claude to output flowing descriptive prose
  (not bullets/JSON, ~150-400 words) since the result is inserted verbatim as
  `campaign_context`, read by `build_system_prompt()` (unchanged) exactly like a
  manually-written prompt.
- `service.py::apply_campaign_brief()` persists the uploaded PDF to
  `data/campaign_briefs/<ingestion_job_id>/brief.pdf` (path derived from
  `ingestion_job_id`, same convention as `src/processing/storage.py::job_dir()` — no
  new DB column for the path) **before** calling Claude, and the raw response to
  `claude_response.txt` in the same directory after a successful call — for
  provenance/debugging, not for the `llm_response_path`-style resume-skips-a-paid-call
  mechanism: this operation has no downstream stage and no automatic retry driver, so
  every invocation is a deliberate re-summarization (an unsupported alternative would
  be to silently skip already-cached PDFs, but two design choices are recorded here as
  a discussion, not a code branch — see task history if reopened).
- New endpoint `POST /ingestion/jobs/{id}/campaign-brief` (multipart PDF upload,
  synchronous — one bounded Haiku call, no `BackgroundTasks` needed unlike ingestion
  validation's arbitrary-latency external URL fetch) and new CLI flag `--campaign-pdf
  <path>` on `python -m src.pipeline.run` (three-way mutually exclusive with the
  existing `--campaign-context`/`--campaign-file`), both calling the same
  `apply_campaign_brief()`.
- `python-multipart` is back in `requirements.txt` (needed for FastAPI's
  `UploadFile`) — this is the same dependency removed alongside the old
  `src/campaign/` module on 2026-07-25, re-added because this time the upload is
  actually parsed and used, not just stored raw (the specific failure mode that got
  the old module archived).
- No new status-tracking model/table (`CampaignBriefJob`) — a single Claude call plus
  one column update doesn't need multi-stage resume tracking; `CampaignBriefError`
  (mirrors `HighlightError`/`ProcessingError`'s `stage`-tagged exception convention)
  plus a plain `ValueError` for "job not found" is sufficient.
**Verification status**: fully verifiable locally, no GPU involved — needs only
`ANTHROPIC_API_KEY` and a real small PDF. Not yet verified against a real vast.ai run:
whether the resulting summary, once flowing through `build_system_prompt()`, actually
produces a measurably different/better set of highlight candidates (same caveat
category as the rest of the campaign-context and highlight-detection work).

**Ingestion form wired up to the campaign-brief PDF endpoint, plus per-request Claude
key and a persisted per-job Hugging Face token (2026-08-01).** The campaign-brief PDF
endpoint added above had never actually been called from `frontend/` — no client
function existed for it. `SubmitJobForm.tsx` now has a native `<details>` "Advanced
options" section (no new dependency) with a PDF file input, a Claude API key field,
and an HF token field; submit does `createIngestionJob()` then, if a PDF was chosen,
`uploadCampaignBrief()` right after — the job appears in the tracked list as soon as
the first call succeeds, so a slow/failed brief summarization doesn't block or hide
the job itself.
- **Claude API key is request-scoped only, never persisted.** It's the only HTTP-
  reachable path that calls Claude today (`highlights/llm.py`'s call is CLI-only, no
  router exists for it) — sent as a `Form` field alongside the PDF
  (`anthropic_api_key`, `src/api/routers/campaign_brief.py`), threaded through
  `apply_campaign_brief()`/`summarize_campaign_brief()` as a plain function
  parameter, and used as `anthropic.Anthropic(api_key=...)` when present (falls back
  to the server's own `ANTHROPIC_API_KEY` env var otherwise). Never logged, never
  written to `claude_response.txt`/DB — deliberately kept out of every place this
  module already persists artifacts.
- **HF token is the opposite tradeoff: persisted, because it's consumed later by a
  separate CLI process.** Unlike the Claude key, nothing today calls
  `transcribe()`/`WhisperModel` over HTTP — it only ever runs inside
  `python -m src.highlights.run` or `pipeline.run`, invoked well after (sometimes a
  different session/instance from) the browser request that created the job. So
  `IngestionJob` gained an `hf_token` column (same persistence pattern as
  `campaign_context`), settable from the form or via new `--hf-token` on
  `pipeline.run` (threading into `create_job()` for a new job, or updating the
  existing job on a `--job-id` resume, mirroring `--campaign-context`'s resume
  handling). `run_highlight_detection()` (`src/highlights/service.py`) exports it as
  `os.environ["HF_TOKEN"]` right before calling `transcribe()` — a deliberate global
  env var mutation, safe here because this always runs as a single-job CLI process,
  never a concurrent server request (the Claude key above is the server-request case,
  which is why it's threaded as an explicit parameter instead). Only set when
  present, so it never clobbers an already-`export`ed instance-level `HF_TOKEN` (e.g.
  vast.ai's own env var) with `None`.
- **`hf_token` is deliberately excluded from `IngestionJobRead`** (`src/ingestion/schemas.py`)
  even though it's stored — unlike `campaign_context`, it's a credential, and
  `GET /ingestion/jobs/{id}` is polled every 3s by `useTrackedJobs`, so echoing it
  back would put it in the network tab on every poll. Same reasoning `raw_metadata`
  already gets for staying out of the response model. Frontend key/token fields use
  `type="password"` and live only in React component state (not `localStorage`) —
  cleared on page reload, a deliberate tradeoff over persisting a secret client-side
  (flagged as an XSS/devtools-inspection risk, not worth it for this project's current
  no-auth, localhost-only API).
- Existing `data/haroclip.db` files need the usual manual migration (see
  `VAST_GUIDE.md`): `ALTER TABLE ingestion_jobs ADD COLUMN hf_token TEXT;` —
  `scripts/entrypoint.sh` applies it automatically and idempotently, same pattern as
  every other column added so far.
**Verification status**: backend changes are straightforward parameter-threading, no
GPU involved — verifiable locally via the FastAPI dev server + `sqlite3` inspection
(confirm `hf_token` lands in the DB row but never appears in the `IngestionJobRead`
JSON) and a real small-PDF campaign-brief call. Whether `HF_TOKEN` actually reaches
`huggingface_hub`'s download call as intended can only be confirmed by inspecting
`os.environ` at the right point locally (heavy ML deps aren't installed in this dev
venv per the earlier verification-caveat notes) — same "needs a real vast.ai run"
caveat as the rest of the transcription/highlight-detection path.

**Per-video output folders for reframe/captions/captioned, closing the last flat
directories (2026-08-01), per user direction.** Of the 8 top-level `data/` output
directories, 5 were already grouped per `ingestion_job_id` (`data/videos/<id>/`,
`data/clips/<id>/`, `data/campaign_briefs/<id>/`, `data/logs/<id>/`); `data/reframed/`,
`data/captions/`, and `data/captioned/` were the only 3 still flat, mixing every
video's files together in one directory (the last of the three had a real filename
collision risk — see the now-resolved "Known accepted limitation" note above).
- `src/reframe/storage.py::reframe_output_path()` and
  `src/captioning/storage.py::caption_ass_path()`/`captioned_output_path()` all
  gained a leading `ingestion_job_id` parameter and now create/return a path nested
  under `<top-level-dir>/<ingestion_job_id>/...` — same convention the other 5
  directories already used, just extended to the last 3. Filenames inside each
  per-job folder are unchanged (`<highlight_clip_id>.mp4`/`.ass`,
  `<title-slug>_captioned_<rank>.mp4`) — only a folder layer was added, not a
  renaming scheme.
- Both call sites (`src/reframe/service.py::run_reframe()`,
  `src/captioning/service.py::run_captioning()`) already had `ingestion_job_id` in
  scope right where the output path is built (it's what `get_job_logger()` was
  already using), so this was a pure parameter-threading change — no new lookups,
  no schema change (`ReframeJob.output_path`/`CaptionJob.ass_path`/`.output_path`
  stay plain `String` columns storing a `DATA_DIR`-relative path, same as before).
- **No migration of already-rendered files** — this only changes where *new* renders
  land; anything rendered before this change stays at its old flat path on disk
  (not moved, not deleted). Acceptable since the project has no real production
  output yet, only local/synthetic verification runs (see the module's own
  verification-caveat notes throughout this doc).
- Considered and rejected: consolidating *all* per-video output (including the 5
  already-correct directories) under one root folder per video (e.g.
  `data/jobs/<id>/{videos,clips,reframed,...}/`). Would also close the mixing
  problem and make a single `scp -r` copy the whole video's results off a vast.ai
  instance, but touches every storage module including ones that already work
  correctly, for a pure ergonomics gain over the smaller fix — out of scope for what
  was asked; revisit only on explicit user request, not as a follow-up assumption.
**Verification status**: verified locally via direct calls to the three updated
storage functions (confirmed output paths nest under the job id as expected) and a
plain import check of both `service.py` modules (no signature-mismatch errors at the
single call site each function has). Not re-run through the full synthetic captioning
regression fixture this session — same "needs a real vast.ai run" caveat as the rest
of the reframe/captioning modules for confirming this holds up on production-scale
output.

**Frontend UI setup automated in `scripts/entrypoint.sh`, gated behind `ENABLE_UI`
(2026-08-01), per user direction after a real vast.ai session hit avoidable setup
friction.** Trying to run `frontend/` directly on a vast.ai instance (to fill out the
ingestion form instead of typing CLI flags) surfaced that the vast.ai PyTorch/CUDA
template has no Node.js/npm at all, and Debian's own `apt` package is too old for
this project's Vite 8/React 19 frontend — both had to be worked around manually,
mid-session, on a rented-by-the-hour instance.
- New block in `scripts/entrypoint.sh`, gated by `if [ "${ENABLE_UI:-0}" = "1" ]`:
  installs Node.js 20.x via NodeSource (only if `node` isn't already on `PATH`),
  runs `npm install` in `frontend/` (only if `node_modules` is missing), and creates
  `frontend/.env` from `.env.example` (only if missing) — same idempotent
  check-then-install pattern already used for `ffmpeg`/`fonts-dejavu-core`/
  `sqlite3`/YOLOv8-face weights earlier in this script.
- **Deliberately gated, not unconditional**: most sessions only need the CLI
  pipeline (see "Constraints" — instances are rented on-demand for GPU work, not
  kept running for a UI), so installing Node.js on every boot would waste time for
  sessions that never touch the frontend. Opt in via `ENABLE_UI=1` in vast.ai's
  "Environment Variables" field, same place `ANTHROPIC_API_KEY`/`HF_TOKEN` already
  go.
- **Deliberately does NOT auto-start `uvicorn`/`npm run dev`** — per user direction,
  only the slow/error-prone *setup* (Node install, `npm install`) is automated;
  starting the two servers stays two explicit manual commands, so the user stays
  aware of when the UI is actually running and consuming the instance's resources.
- `VAST_GUIDE.md` gained a new "Using the browser UI on vast.ai (optional)" section
  (now step 4, pushing "Collect results"/"What to check in the logs" to steps 5/6)
  documenting the full flow end to end, including the specific SSH-tunnel mistake
  hit in the real session that prompted this change: the tunnel command must run
  from a **new terminal on the user's own local machine**, not from the SSH session
  already controlling the instance — and it requires an SSH key registered on the
  vast.ai account, which isn't needed for vast.ai's own Web Terminal access.
**Verification status**: `bash -n scripts/entrypoint.sh` syntax-checked clean.
Installing Node.js/running `npm install`/opening a real SSH tunnel can only be
verified on an actual vast.ai instance by the user — not something reproducible on
this local dev machine, so this is unverified beyond syntax until the next real
vast.ai session.

**Real vast.ai run surfaced a second highlights-stage LLM bug: `claude-sonnet-5` has
no way to cap thinking token spend, so `MAX_NEW_TOKENS` needed a much bigger ceiling
(2026-08-01).** A real ~57-minute video's transcript on a real vast.ai run failed at
the detection stage with the same class of error the 2026-07-26 LLM switch's
`MAX_NEW_TOKENS` bump (2048→8192) was meant to prevent — Claude's adaptive thinking
consumed the *entire* 8192-token ceiling before writing any JSON, so
`generate_candidates()` (`src/highlights/llm.py`) again hit
`stop_reason == "max_tokens"` with an empty text response. Root cause this time isn't
"the ceiling was too low for the old failure mode" — it's that **`claude-sonnet-5`
(and the rest of the current-generation Claude 4.6+ family) removed the
`budget_tokens` parameter that older models used to cap thinking directly**; sending
it now returns a 400. Adaptive thinking has no fixed budget of its own, so the only
remaining lever against "thinking ate the whole response budget" is a generous
`max_tokens` ceiling — there's no way to bound thinking independently anymore.
- `MAX_NEW_TOKENS` raised `8192`→`32000` — comment updated to explain the
  `budget_tokens` removal rather than just restating "leaves headroom," so a future
  reader doesn't reach for `budget_tokens` as a fix and hit the same 400.
- **`generate_candidates()` switched from `client.messages.create()` to
  `client.messages.stream()` + `.get_final_message()`** — a `max_tokens` this large
  crosses the Anthropic SDK's own non-streaming safety guard, which raises a
  client-side `ValueError` *before sending a request* if it estimates a non-streaming
  call could run past ~10 minutes. `get_final_message()` returns the identical
  `Message` shape `.create()` did (`.content`, `.usage`, `.stop_reason`), so no
  downstream code (`parse_candidates`, the cached-response write, etc.) needed to
  change.
- No prompt or validation logic changed — this is purely a request-shape/ceiling fix,
  same category as the original 2026-07-26 bump, not a hook-quality change.
**Verification status**: syntax/import-checked locally (no local `ANTHROPIC_API_KEY`
to smoke-test the real streaming call — same limitation noted throughout this
module's other Claude API work). Confirming this actually clears the real transcript
that triggered it requires re-running `python -m src.pipeline.run --job-id
1b99fdb1-f128-4d6f-af16-5e690068a8d3` (or `python -m src.highlights.run --job-id
1b99fdb1-f128-4d6f-af16-5e690068a8d3`) on the vast.ai instance — the ingestion/
processing stages for that job already completed successfully before this failure,
so `--job-id` resumes straight into the highlights stage without re-downloading.

**Highlight candidate count target raised to a content-driven "up to 15", per user
direction (2026-08-01).** Previously the prompt's output contract
(`src/highlights/prompt.py`) told Claude to "return between 5 and 10 candidate
clips" — a rigid numeric range — and `MAX_CANDIDATES = 10` (`src/highlights/llm.py`)
silently truncated any longer response to 10 (`parse_candidates`'s
`valid[:MAX_CANDIDATES]`). User's stated goal was more clips per video (10-15), but
explicitly rejected simply swapping one rigid range for another
("Buat logika yang longgar, keputusan jumlah clip dilihat dari isi konten juga, jadi
keputusan banyaknya jumlah klip dilakukan oleh claude juga") — the count should be
Claude's own judgment call based on how much genuine hook material a specific video
actually contains, not a target forced regardless of content.
- Prompt's output contract now reads: return as many genuinely hook-worthy
  candidates as the content supports, up to a maximum of 15, explicitly framing a
  dense/eventful video as commonly landing in 10-15 while a shorter/less eventful one
  may genuinely only have a handful — and explicitly forbids padding the list with a
  weak/borderline candidate just to reach a higher count. No forced minimum anymore
  (the old prompt's implicit floor of 5 is gone).
- `MAX_CANDIDATES` raised `10`→`15` in `src/highlights/llm.py` — kept as a defensive
  ceiling in code (protects downstream reframe/captioning render cost from an
  unbounded response), not reintroduced as a target; comment updated to say so
  explicitly.
- `MAX_NEW_TOKENS` (`32000`) comment's stale "5-10-candidate" reference updated to
  match; the token math itself was already generous enough to cover a few more
  candidates' worth of JSON without needing to change.
- No other file changed — same reasoning as the jump-cut/snapping fixes above: every
  downstream stage (`service.py`'s render loop, `reframe/`, `captioning/`,
  `pipeline/run.py`) already queries/iterates `HighlightClip` rows with no count
  assumption or `.limit()`, so this is fully contained to the prompt text and one
  constant.
**Verification status**: no local `ANTHROPIC_API_KEY` to smoke-test the real prompt
change (same limitation as the rest of this module's Claude API work). Whether
Claude's actual candidate-count judgment in practice tracks real content richness
(rather than defaulting to either extreme) is unverified — needs a real vast.ai run,
same caveat category as the rest of highlight-detection quality (see the "Next
steps" verification list below, which should be read as covering this too).

**Caption font overhauled — FontSize 36→90, font switched DejaVu Sans Bold→Rubik
Bold (vendored static instance), word-wrap overflow bug fixed, and a per-word
background-highlight caption style prototyped then shelved (2026-08-01), driven
by real user review of the `dc4fbe55` vast.ai run's actual output.** After
watching the real burned captions from that run, user feedback was that `36`
(the 2026-07-28 image-verified value) still read as small compared to typical
viral-caption conventions — prompting a second real-render sizing pass, this
time iteratively burning candidate sizes onto a real vertical clip locally
(`data/vast/reframed/03ea78c1-...mp4`, a leftover 5s clip from the first
buggy vast.ai run — real footage, fine for a pure sizing/legibility check even
though its own duration is a known artifact of that earlier bug) rather than
guessing from a formula, same practice as the original 14→72→36 pass:

- **FontSize final = 90`** (`src/captioning/subtitles.py`'s `write_ass`
  default), chosen after real burns at 36/42/60/90/120/180/270/320 — 270 and
  320 both visibly clipped off both frame edges even for a short 3-word cue,
  180 was flush against the edges (no margin left), 90 was clearly more
  dominant than 36 while still leaving comfortable side margin for a normal
  2-4 word burst cue.
- **Local testing surfaced a real, previously-unnoticed overflow bug**: burning
  a deliberately long (22-word) test cue at the new larger size showed
  `WrapStyle: 2` (the `ASS_TEMPLATE` setting since this file was first
  written) means "no automatic word wrap, only explicit `\N` breaks" — a cue
  wider than the frame doesn't wrap to a second line, it silently overflows
  and gets clipped off both edges with no visual indicator anything was cut.
  Switched to **`WrapStyle: 0`** ("smart" wrapping, evenly split, top line
  wider) — real-render-verified the same 22-word cue now correctly wraps into
  multiple centered lines with nothing clipped. `build_burst_cues` already
  caps normal cues at 4 words/1.5s so this is mainly a safety net (a single
  unusually long word could still trigger it), not an expected everyday case.
- **Font switched to Rubik**, per user direction — confirmed via web search to
  be SIL Open Font License 1.1 (official Google Fonts family, no Reserved
  Font Name declared), same license category as DejaVu Sans. Getting it
  working correctly took real troubleshooting, itself a useful record:
  - Google's own font repo (`google/fonts`, `ofl/rubik/`) ships Rubik **only
    as a variable font** (`Rubik[wght].ttf`, `wght` axis 300–900) — unlike
    DejaVu, there's no separate static per-weight file upstream.
  - Installing that variable font locally (Windows, per-user font install —
    copy to `%LOCALAPPDATA%\Microsoft\Windows\Fonts\` +
    `AddFontResourceW` + `WM_FONTCHANGE` broadcast, all needed since a bare
    registry entry alone isn't picked up by the current session) and
    requesting `"Rubik"` or `"Rubik Bold"` from ffmpeg/libass **silently fell
    back to Arial** — inspected via `fontTools`: the font's legacy family
    name (nameID 1) is `"Rubik Light"` (the default named instance, weight
    300); Bold/Medium/SemiBold/etc. exist only as `fvar` axis positions, not
    separate legacy family names, so a non-variable-aware matcher (confirmed
    for libass's DirectWrite backend on Windows) can't resolve them by name
    at all. Requesting `"Rubik Light"` specifically did resolve correctly,
    proving the loading mechanism itself worked — just not the weight wanted.
  - **Fix**: used `fonttools varLib.instancer` to freeze the `wght=700`
    instance into a standalone static font, with its name-table records
    renamed to `"Rubik Bold"` / `"Rubik-Bold"` — the same "name the bold
    weight directly, sidestep matching ambiguity" trick the retired DejaVu
    convention used, just applied via font-generation instead of relying on
    an upstream-shipped file. Real-render-verified:
    `fontselect: (Rubik Bold, 400, 0) -> Rubik-Bold, 0, Rubik-Bold` (no
    fallback) after the rename, vs. falling back to Arial before it.
  - The generated `Rubik-Bold-static.ttf` (~212KB) is **committed to git**
    under `src/captioning/fonts/`, alongside `OFL.txt` (full upstream
    license text) and `NOTICE.md` (provenance, the variable-font-ambiguity
    story above, and the exact `fonttools` snippet to regenerate it) — same
    vendoring pattern as `src/detection/light_asd/`'s committed checkpoint +
    `LICENSE`/`NOTICE.md`, chosen over downloading/generating it at
    build/entrypoint time since it's small, deterministic, and needs no
    network access or `fonttools` as a runtime dependency.
  - **`fontsdir` (ffmpeg's `subtitles` filter option) loads the font directly
    from `src/captioning/fonts/`** (`FONTS_DIR` in `src/captioning/service.py`,
    passed alongside the existing `subtitles=` filter argument) — discovered
    while debugging the above, and adopted as the loading mechanism instead
    of an OS-level font install entirely. This is a strict simplification
    over the old approach: **`fonts-dejavu-core` is no longer a system
    prerequisite at all** — removed from `Dockerfile`, `scripts/entrypoint.sh`,
    and `VAST_GUIDE.md` (all three previously had a check-then-`apt-get
    install fonts-dejavu-core` step; none of them need any font-related step
    now, since `COPY . .` / `git clone` already brings the committed font
    file along, and `fontsdir` doesn't need it OS-installed).
- **A per-word background-highlight caption style was prototyped, then
  shelved by user direction ("kita tunda rancangan ini, sepertinya perlu
  proses yang panjang") — not an open "revisit if X" item, recorded here as
  considered-and-closed, same as the earlier custom-Pillow-renderer
  discussion.** The ask was: instead of the current `\k` karaoke effect
  (spoken word's *text color* changes), give the spoken word a highlighted
  *background box* while text color stays constant throughout. Standard ASS
  override tags can't do this (no inline way to toggle `BorderStyle`'s opaque
  background mode per word within one `\k`-timed line), so a working
  prototype was built and real-render-verified successfully: `Pillow`
  (already available, no new prod dependency needed for this narrow
  measurement-only use — a deliberately smaller ask than the shelved
  full-custom-renderer idea) measures each word's real pixel width against
  the actual `Rubik-Bold-static.ttf` at the target size, then two ASS
  `Dialogue` lines are emitted per word: a lower-layer `\p1` vector-drawn
  filled rectangle timed to exactly that word's `[start, end]` (no `\k`
  needed — plain per-line timing does the on/off), plus an upper-layer text
  run in one constant color spanning the whole cue. Real burn confirmed the
  box correctly follows the currently-active word with text color never
  changing. **Known unresolved gap at shelving time**: this prototype's
  manual single-line layout has no wrap fallback (unlike the `WrapStyle=0`
  fix above) — a cue whose total measured width exceeds the frame can overflow
  past the margin, real-render-confirmed with a longer 4-word test cue.
  Never touched production code — entirely scratchpad experimentation, no
  cleanup needed. Revisit only on a future explicit user request.
**Verification status**: all of the above (WrapStyle fix, Rubik Bold
resolution, `fontsdir` loading) is real-render-verified, but **only on this
Windows dev machine** — the actual production font-matching backend on
vast.ai is Linux/fontconfig, not Windows/DirectWrite. Risk is judged low
since `Rubik-Bold-static.ttf`'s renamed, unambiguous ASCII family name is the
same kind of name any font matcher (fontconfig included) resolves trivially,
and `fontsdir` is a documented, backend-agnostic libass/ffmpeg feature — but
per this project's standing caveat category, it's still unconfirmed until a
real vast.ai run actually burns a clip and the output is inspected.

**Reframe crop-center-drifts-between-two-people bug fixed with a two-layer,
track-continuity-aware selection/interpolation fix (2026-08-01), per user
report.** User observed: when two people are close together in a frame, the
dynamic-crop camera's center ends up between the two people instead of locked
onto one. Root cause traced by code inspection (not yet confirmed against
real two-person footage): neither `src/reframe/crop_path.py` nor
`src/reframe/speaker_selection.py` ever explicitly averages two people's
positions — `build_crop_path()` always operated on a single, already-filtered
`track_id`'s boxes. The actual mechanism is an **undetected ByteTrack ID
switch** (`src/tracking/face_tracker.py`, `sv.ByteTrack()` default config,
untouched by this fix): when two faces are close/overlapping, the same
`track_id` can silently start referring to a different physical person
mid-clip. `select_primary_track_with_asd()` picked one `track_id` for the
whole clip based on a mean ASD score across all its samples, unaware the
identity underneath had switched; `build_crop_path()`'s `np.interp()` then
linearly interpolated straight from person A's last sample to person B's
first sample across the switch point, and the existing 15-frame moving
average further blended x-values straddling it — producing a crop center
that visibly sits between the two people for several frames.

Fix, per explicit user-selected direction (a combined two-layer approach, not
ByteTrack tuning):
- **New shared module `src/reframe/track_continuity.py`**: `find_discontinuities()`
  computes frame-to-frame horizontal velocity (px/sec, normalized by
  `frame_index` delta ÷ fps rather than a raw per-sample pixel delta, so the
  threshold means the same thing regardless of clip fps or sampling gaps) and
  flags any jump above `MAX_JUMP_PX_PER_SEC = 1800.0` — an **untuned
  heuristic default**, no real two-person footage exists locally to calibrate
  against. `split_into_segments()` splits a sorted sample list at each
  detected break; zero breaks returns the input unchanged (`[samples]`),
  making both call sites below regression-safe by construction for clips with
  no ID switch.
- **`src/reframe/crop_path.py`**: `build_crop_path()` gained a required `fps`
  parameter. New `_interpolate_with_segment_breaks()` computes the existing
  flat `np.interp()` as a bulletproof base, then — only if
  `split_into_segments()` finds more than one segment — overwrites each
  segment's own frame range with its own independent `np.interp()`, with the
  boundary between segments placed at the frame midpoint between the two
  segments' nearest real samples and **no interpolation drawn across the
  gap** (a hard cut, not a single-point clamp — clamping one value can't fix
  a multi-frame linear-ramp problem). Wrapped in try/except, falling back to
  the plain flat `np.interp()` (today's exact pre-fix behavior) on any
  failure. The existing 15-frame `_moving_average()` still runs on top,
  unchanged, and will smooth a short (~±7 frame) transition around the hard
  cut — a deliberate, accepted residual, not fixed further in this pass.
- **`src/reframe/speaker_selection.py`**: `LightASDScorer.score_track()`
  (`src/reframe/asd_scoring.py`) was confirmed to already operate generically
  on whatever `frame_index` range its input boxes span — it needed **zero
  changes** to score an arbitrary sub-segment of a track instead of a whole
  `track_id`. New `TrackSegment` dataclass (`track_id`, `segment_index`,
  `boxes`) and `_build_segments()` group tracks by `track_id` (same pattern
  as before), then run each group through `split_into_segments()`.
  `ASD_MIN_TRACK_SAMPLES` now filters **per segment** rather than per whole
  `track_id`. **`select_primary_track_with_asd()`'s return type changed from
  `int | None` to `list[TrackedFace]`** (empty list for "no primary," not
  `None`) — it now returns the winning segment's boxes directly rather than a
  track_id for the caller to re-filter by, which lines up cleanly with
  `build_crop_path()`'s existing `if not primary_track_boxes` empty-list
  guard. The pure heuristic `select_primary_track()` (largest total bbox
  area) is **completely unchanged** — wrapped in a new `_heuristic_fallback()`
  that adapts its `int | None` result to the new `list[TrackedFace]` shape —
  preserving the exact same fallback safety net (ImportError, no scorable
  candidates, model-load failure, no usable score) with no new failure mode
  that could crash a whole reframe job. If segment-splitting itself throws, a
  narrower fallback treats each `track_id` as one unsplit segment (today's
  pre-fix grouping) rather than jumping straight to the full heuristic — ASD
  can still score meaningfully on an unsplit track.
- **`src/reframe/service.py`**: `run_reframe()` updated mechanically for both
  signature changes — `select_primary_track_with_asd()` now returns boxes
  directly (the old `primary_track_id` + re-filter step is gone), and
  `build_crop_path()` is called with the now-required `fps` argument.

**Known limitation, stated explicitly rather than glossed over**: this is a
position-jump-velocity heuristic. In the bug's tightest form — two faces that
are genuinely close together or overlapping — the center-to-center jump at
the actual switch instant may itself be small, so `MAX_JUMP_PX_PER_SEC` may
fail to catch exactly that case. This is a mitigation for switches with
enough lateral separation to look like a jump, not a guaranteed fix for every
occurrence of the reported symptom.

**Verified locally** (hand-built `TrackedFace` fixtures, no CUDA GPU, no real
two-person footage — same verification-style limitation as the rest of this
module): `find_discontinuities`/`split_into_segments` against a fixture with
smooth motion (no false-positive break) and an injected large jump (break
detected at the correct index, splits into the correct segment count for
both single- and multi-break cases); `fps <= 0` doesn't crash. `build_crop_path()`
on the same injected-jump fixture confirmed the **pre-smoothing** interpolated
array now holds each person's value flat right up to the segment boundary and
jumps directly to the other person's value (no ramp across the gap) — verified
by direct inspection of `_interpolate_with_segment_breaks()`'s output frame-by-
frame; a no-discontinuity fixture produces byte-identical output to a
manual re-implementation of the pre-fix flat-interp+smooth+clip code path
(regression safety); empty boxes still freeze-center; forcing
`split_into_segments` to raise confirmed the try/except fallback still
returns a full-length path. `select_primary_track_with_asd()` against the
same fixture (real CPU-fallback `LightASDScorer`, weights load correctly)
confirmed segment grouping/per-segment `ASD_MIN_TRACK_SAMPLES` filtering/
per-segment scoring-and-logging all wire up correctly, and that every
fallback branch (no video file to score against, too-few-samples-per-segment,
forced segment-split failure, empty tracks) returns the new `list[TrackedFace]`
shape without crashing — real ASD *scores* are still meaningless without a
real video file, same pre-existing gap already documented earlier in this
file. Full chain (`_detect_and_track` → `select_primary_track_with_asd` →
`build_crop_path`) re-run end-to-end against a synthetic zero-face test clip,
confirmed no crash and correct output shapes with the new `fps` plumbing
in place.

**Not yet verified, needs a real vast.ai run with real two-person footage**
(same caveat category as the rest of this module): whether
`MAX_JUMP_PX_PER_SEC = 1800.0` actually distinguishes a real ID switch from
legitimate fast head motion — the single biggest open risk, especially for
the close/overlapping-faces case noted above; whether real (non-CPU-fallback)
Light-ASD scores meaningfully discriminate between two segments of a split
track; whether the rendered output actually looks visibly fixed (no fixture
can prove this, only real two-person footage can); real-world frequency of
ByteTrack ID switches, which affects whether the per-segment
`ASD_MIN_TRACK_SAMPLES` filter is too aggressive or not aggressive enough in
practice.

**Reframe crop freeze/center-lock on 3+-person podcast footage fixed —
segment-level fallback tier added, ByteTrack/threshold levers considered and
mostly rejected (2026-08-01).** Real user report, same day as the
two-person crop-drift fix above: on a podcast-format video with more than 2
people on screen, almost all generated clips showed the crop **frozen/
centered in the middle of the frame** for stretches instead of following any
speaker — user-confirmed symptom (not "locks onto the wrong person").

Root cause, traced by direct code reading: `build_crop_path()`
(`src/reframe/crop_path.py:31-33`) freeze-centers only when the
`primary_track_boxes` it receives is **empty**. Tracing upward,
`select_primary_track_with_asd()` (`src/reframe/speaker_selection.py`) had 4
fallback branches (Light-ASD `ImportError`, no segment clears the sample
floor, model-load failure, no segment produces a usable score), and **all
four** collapsed to `_heuristic_fallback(tracks, log)` — the **raw, unsplit,
whole-`track_id`** heuristic, discarding the continuity-segment work
entirely. The critical line: `candidates = [seg for seg in segments if
len(seg.boxes) >= ASD_MIN_TRACK_SAMPLES]` (`ASD_MIN_TRACK_SAMPLES = 3`) —
each individual continuity *segment* (not whole track) needs ≥3 samples
(≥0.6s unbroken same-identity tracking at `SAMPLE_FPS=5`) before it's even
eligible for ASD scoring. With 3+ people in frame, more ByteTrack ID churn
(more IoU-association ambiguity with more simultaneous close faces) and/or
more `track_continuity.py` splits produce many short segments, so most/all
candidates fail the floor — and once none clear it, the code fell back to
the **raw track**, which in a crowded scene can itself be short/fragmented
enough to end up empty by the time it reaches `build_crop_path()`.

**Real evidence, not just theory**: the actual vast.ai run's `reframe.log`
(`data/VAST/logs/logs/dc4fbe55-d897-45ae-90c0-0885a0a70f62/reframe.log`,
predates this fix and the two-person crop-drift fix — old whole-track
`method=light-asd track_id=X score=Y` log format, no segment splitting yet)
shows **11 to 28 unique ByteTrack IDs per individual highlight clip** across
all 11 clips of that video — direct confirmation this is a heavily
multi-person/high-ID-churn video, exactly the regime where a per-segment
3-sample floor with no intermediate fallback tier starves candidates. (That
old run never actually froze — the pre-fix code scored whole raw tracks
directly with no segment floor — which is itself informative: the freeze
risk was introduced by today's earlier segment-splitting fix, not
pre-existing, and this second fix closes the gap it opened.)

**One correction to the initial hypothesis, made after reading the code
rather than assumed**: the claim that `MAX_JUMP_PX_PER_SEC` "over-triggers"
in crowded scenes is not clearly true and may run backwards — in a crowded
frame, people sit physically closer together on screen, so a real
undetected ID switch between two adjacent people produces a **smaller**
pixel jump than in a tight two-person close-up, i.e. crowding plausibly
makes the existing threshold *less* likely to catch a real switch
(under-splitting), not more likely to false-trigger on legitimate motion.
The genuinely solid part of the hypothesis is downstream: the per-segment
sample floor plus a fallback that discards segmentation entirely.

Decisions, lever by lever (targeted-fix scope only, per explicit user
direction — a rearchitecture toward dynamic multi-speaker-follow-within-one-
clip was explicitly declined as a separate future item, not bundled here):

- **`src/tracking/face_tracker.py`**: `sv.ByteTrack()` → `sv.ByteTrack(
  minimum_consecutive_frames=2)` — the one change made. Filters a track that
  only ever appeared in a single sampled frame (a flicker, more likely with
  more faces on screen) before it counts as "activated," at zero cost to a
  real speaker candidate (persists for seconds, not one sample).
  `track_activation_threshold` (already moot — `FaceDetector.
  CONFIDENCE_THRESHOLD=0.5` already exceeds both it and the derived
  `det_thresh`), `minimum_matching_threshold` (0.8 is upstream ByteTrack's
  own validated value, including on MOT20, a genuinely dense-crowd
  benchmark — already tuned for this), and `lost_track_buffer`/`frame_rate`
  (default 30/30 against 5fps-sampled calls gives ~6s of real occlusion
  tolerance, which *helps* in a crowded scene's frequent mutual occlusion;
  "fixing" the unit mismatch to `frame_rate=5` would shrink that to ~1s, a
  real regression with no offsetting benefit) were all considered and
  explicitly left unchanged, not overlooked.
- **`MAX_JUMP_PX_PER_SEC` (`src/reframe/track_continuity.py`): unchanged.**
  Raising it risks reopening the under-splitting bug the two-person fix
  documented as a known limitation (more likely, not less, in a crowded
  frame per the correction above); lowering it risks over-splitting on
  legitimate fast motion; scaling it by concurrent-track-count was
  considered but rejected — no local ground truth to determine which
  direction is net-beneficial. Made low-stakes by the fix below: whether a
  track splits into 2 segments or 6, the new fallback tier guarantees a
  real, non-empty, largest-known-face segment is used either way.
- **`src/reframe/speaker_selection.py` (the actual fix)**: `segments =
  _build_segments(tracks, source_fps, log)` now built once near the top of
  `select_primary_track_with_asd()` — right after the `if not tracks: return
  []` guard, before the `LightASDScorer` import attempt, so it's available
  to *every* fallback branch including the `ImportError` one (previously
  skipped continuity-splitting entirely, a separate robustness gap closed
  for free here). New `_segment_heuristic_fallback(segments, log)`: picks
  the single best `TrackSegment` by cumulative bbox area (tie-broken by
  sample count) — never a raw/unsplit track_id — and is guaranteed
  non-empty whenever `segments` is non-empty, which `_build_segments`
  guarantees whenever the source `tracks` list was non-empty (every
  `track_id` yields ≥1 segment even with zero discontinuities). All 4
  `_heuristic_fallback(tracks, log)` call sites inside
  `select_primary_track_with_asd` now call `_segment_heuristic_fallback(
  segments, log)` instead. `select_primary_track()`/`_heuristic_fallback()`
  (the raw-track versions) are kept, unremoved — still meaningful as a
  standalone documented heuristic — just no longer reached from this
  function's fallback chain. Net effect: `primary_track_boxes` reaching
  `build_crop_path()` is now empty only when **zero faces were detected
  anywhere in the clip**, not merely "no segment happened to clear the ASD
  eligibility floor."
- **Constant unification**: the duplicate `ASD_MIN_TRACK_SAMPLES = 3` in
  `speaker_selection.py` is gone, replaced with `from src.reframe.asd_scoring
  import MIN_TRACK_SAMPLES as ASD_MIN_TRACK_SAMPLES` — canonical definition
  stays in `asd_scoring.py` (the module that actually enforces it physically
  against Light-ASD's MFCC/visual windowing). Safe to import at module level:
  `asd_scoring.py` has no `torch`/`python_speech_features` at its own
  top-level (those stay lazily imported inside `LightASDScorer`), so this
  can't trigger the `ImportError` the existing lazy-import pattern guards
  against. The value itself (3) is unchanged — it's a model-input-validity
  floor, a different concern from "is there a usable segment at all," which
  the new fallback tier now handles independently.
- **`src/reframe/crop_path.py`: unchanged.** With the fallback tier above,
  the freeze-center branch is reachable only when genuinely zero faces
  exist in the clip — there's no "largest-known face position" to prefer
  over dead-center in that state, so touching this file now would only mask
  a symptom that no longer has a real cause. One residual, explicitly
  out-of-scope gap: if the selected segment covers only a fraction of the
  clip, `np.interp`'s edge-hold still freezes the crop at that segment's
  boundary value for frames outside its span — pre-existing "freeze-pan"
  behavior from before this fix; improving it would mean blending across
  multiple speaker segments over time, which is exactly the
  dynamic-multi-speaker-follow rearchitecture the user already declined to
  scope into this fix.
- **New logging** (`speaker_selection.py`): `method=none reason=no face
  tracks detected in clip` on the previously-silent `if not tracks: return
  []` path; the new fallback tagged `method=segment-heuristic` (distinct
  from the old `method=heuristic` tag still used by the untouched
  `select_primary_track()`/`_heuristic_fallback()` pair) so a real run's log
  can tell "no eligible segment, fell back to largest-known-face segment"
  apart from the old whole-track heuristic; a `segment summary: %d raw
  tracks -> %d continuity segments (%d/%d eligible for ASD, floor=%d)` line
  right after `_build_segments` to directly confirm/deny the fragmentation
  hypothesis from real logs; an `ASD segment scoring: %d/%d candidate
  segments produced a usable score` aggregate in the scoring loop, closing
  the prior gap where individual segment-scoring failures were logged one at
  a time with no summary.

**Verified locally** (hand-built fixtures, no CUDA GPU, no real
multi-person footage — same limitation as the rest of this module; scripts
run ad hoc against the project venv, this project has no committed pytest
suite): a synthetic 3-track, heavily-fragmented fixture (every segment under
`ASD_MIN_TRACK_SAMPLES=3`) confirmed `select_primary_track_with_asd` returns
a non-empty result equal to the largest-area segment's boxes, both via the
natural "no eligible candidates" path and via a forced `ImportError` on
`LightASDScorer` (both previously would have exercised the raw-track
fallback); a single-unfragmented-track fixture and a two-clean-track
(no-discontinuity) fixture both confirmed **identical selection output**
before and after this change (regression safety, since 3 of the 4 fallback
call sites now call a different function); the "no track segment produced a
usable ASD score" branch was exercised for real (not mocked) via a fixture
whose lone segment cleared the sample floor but had no real video to score
against, correctly falling through to `_segment_heuristic_fallback`;
`ASD_MIN_TRACK_SAMPLES is asd_scoring.MIN_TRACK_SAMPLES` confirmed as the
same object; `FaceTracker(minimum_consecutive_frames=2)` against the real
locally-installed `supervision==0.29.1` confirmed a single-sample flicker
never appears in `update()`'s output at all, while a persistent multi-frame
face still activates and keeps exactly one `track_id` throughout (no
spurious re-activation), and the existing "empty detections doesn't crash"
smoke test still passes; the full `_detect_and_track` →
`select_primary_track_with_asd` → `build_crop_path` chain re-run end-to-end
against a synthetic zero-face clip confirmed the `method=none` log line
fires and the expected (correct, since genuinely no faces exist) dead-center
freeze fallback still triggers, with no crash and no signature-mismatch
against `src/reframe/service.py`'s unchanged call sites.

**Not yet verified, needs a real vast.ai run with real 3+-person footage**
(same caveat category as the rest of this module): whether
`method=segment-heuristic` actually fires on real 3+-person clips and how
often (watch the log line introduced above); whether the crop visibly stops
freezing dead-center on those clips; whether `MAX_JUMP_PX_PER_SEC=1800.0`
still shows evidence of under- or over-splitting on genuinely crowded real
footage (same open item carried over from the two-person fix, now more
directly relevant given the 11-28-tracks-per-clip real evidence above);
whether the segment-heuristic tier's area-based pick (no active-speaker
signal at all) is a visually reasonable "primary speaker" choice in
practice, versus a real Light-ASD-scored segment.

**Dynamic multi-speaker-follow added — a clip's crop can now switch which
person it follows mid-clip, instead of locking one "primary speaker" for
the entire duration (2026-08-01), per explicit user request after real
evidence showed this was the actual root cause of the originally-reported
podcast tracking complaint.** After the two fixes above shipped, a real
vast.ai run (job `887cd489-0e5e-4104-93de-825b38adab94`, log at
`data/VAST/logs/887cd489-0e5e-4104-93de-825b38adab94/reframe.log`) was
inspected: neither fix's failure mode occurred (all 12 clips resolved via
real `method=light-asd`, and `MAX_JUMP_PX_PER_SEC` never fired once across
all 12 clips — "raw tracks" count exactly equalled "continuity segments"
count in every log line). But visual inspection of the actual rendered
output (`data/VAST/captioned/887cd489-.../tepe_lagi_tepe_lagi_captioned_11.mp4`,
frames extracted at t=3s/10s/~19.7s/25s) revealed the real bug: the crop sat
**frozen exactly between two people** (centered on a microphone stand) for
15+ seconds, never panning even as the active speaker audibly and visibly
changed (left person mid-sentence at t=10s; right person clearly speaking
"aku yang bayar. Gak" at t=25s). The log for that clip:
```
segment summary: 15 raw tracks -> 15 continuity segments (14/15 eligible for ASD, floor=3)
Light-ASD scores per track segment: {'1.0': -1.03, '2.0': 0.476, '3.0': 0.908, ...}
method=light-asd track_id=3 segment=0 score=0.908
crop path: primary_track_id(s)={3} pan range x=[542, 571] (width=1920, crop_w=608)
```
`track_id=3 segment=0` is one unsplit continuity segment spanning the
**entire** 34.3s clip, with a 29px pan range — visually static. Root cause:
the pipeline picked exactly one "primary speaker" for a clip's whole
duration, once, and never revisited that choice — a fundamentally poor fit
for a podcast where people trade turns talking within a single 30-75s
highlight clip. (Whether `track_id=3` is itself a single ByteTrack ID
quietly hovering between two adjacent faces, rather than genuinely
following one stable speaker, remains an open question this fix can't
settle by itself — see the new diagnostic logging below, added specifically
to answer this from the next real run's log rather than needing another
frame-extraction session.)

User was asked earlier the same day whether to scope this in, initially
declined (wanted the smaller freeze-fix scoped first), then after seeing
this real evidence explicitly chose to reopen it ("Buka lagi opsi dynamic
multi-speaker-follow").

**The technical foundation for this was mostly already in place and
partly wasted**: `LightASDScorer.score_track()` (`src/reframe/asd_scoring.py`)
already returns a **per-frame** score sequence (one score per synthetic
`MODEL_FPS=25` fps frame), not a single aggregate — the pre-existing code
just collapsed it to one `mean_score` and discarded all temporal
resolution. `crop_path.py` already had `_interpolate_with_segment_breaks()`'s
"independent interpolation per span + hard cut at the boundary" pattern,
which only needed generalizing from continuity-segment boundaries to
speaker-window boundaries. `renderer.py` needed **zero changes** — confirmed
it only ever consumes a flat `list[tuple[int,int]]`, fully agnostic to how
it was built.

- **New `SpeakerWindow` dataclass** (`src/reframe/speaker_selection.py`,
  next to the existing `TrackSegment`): `start_frame`, `end_frame`
  (exclusive), `track_id`, `segment_index`, `boxes` (the *entire* winning
  segment's samples, not clipped to the window, so edge-hold/interpolation
  at a window's own boundaries behaves like the existing per-segment
  pattern). A list of these always covers `[0, total_frames)` with no
  gaps — the old whole-clip single-speaker selection is just the
  degenerate one-window case.
- **`_score_frame_indices(seg, num_scores, source_fps)`**: maps
  `score_track()`'s per-frame score index back to the clip's real
  `frame_index` space. `asd_scoring.py`'s `_build_visual_feature` samples at
  `t_start + i/MODEL_FPS` (`t_start = seg.boxes[0].frame_index / source_fps`)
  and `_run_ensemble` only ever truncates from the end, so `scores[i]`
  always corresponds to that same sample `i` — **no changes needed in
  `asd_scoring.py` at all**, every input this mapping needs was already
  available to the caller.
- **`_score_candidates(scorer, video_path, candidates, source_fps, log)`**:
  extracted from the old inline scoring loop — scores every candidate
  segment exactly once (same Light-ASD calls, same count, **zero new
  inference or audio-extraction cost**), returning each one's full
  per-frame score sequence instead of immediately collapsing it. Shared by
  both the new timeline builder and the old-behavior fallback below, so
  Light-ASD is invoked from exactly one place.
- **`_build_speaker_windows(candidates, scores_by_seg, fps, total_frames, log)`**
  — the core new logic, a winner-take-all sweep with hysteresis:
  - At each frame, a candidate is "active" only within its own observed
    score-curve span (no extrapolation beyond it); the raw winner is
    whichever active candidate scores highest right now.
  - A frame with no active candidate at all **holds the incumbent**
    (gap-hold — same edge-hold convention `np.interp`/this module already
    use elsewhere).
  - A switch only commits once a single challenger has led the
    incumbent's own **live** score (tracked every frame the incumbent has
    coverage, not frozen at its last winning moment — an actual bug caught
    during local verification, see below) by `>= SCORE_MARGIN` for
    `>= MIN_DWELL_SECONDS` of *consecutive* frames — this absorbs momentary
    ASD noise or a short interjection ("iya", a laugh) without triggering a
    real speaker switch. The resulting window boundary is backdated to
    where the challenge *began*, not where the dwell streak was confirmed,
    so the crop starts moving toward the new speaker as soon as their
    dominance is later confirmed to have started, rather than lagging an
    extra `MIN_DWELL_SECONDS` behind it.
  - `MIN_DWELL_SECONDS = 1.5` and `SCORE_MARGIN = 0.3` are **both untuned
    heuristics**, flagged explicitly the same way `track_continuity.py`'s
    `MAX_JUMP_PX_PER_SEC` already is — no real multi-speaker footage exists
    locally to calibrate against. `MIN_DWELL_SECONDS` loosely anchors off
    `src/captioning/subtitles.py`'s own existing 1.5s cue-closing threshold
    as an order-of-magnitude guess for "a natural minimum unit of continuous
    speech" in this codebase, not a principled derivation.
    `SCORE_MARGIN = 0.3` is roughly 8% of the raw Light-ASD logit range
    actually observed in the real log above (roughly -2.9 to +0.9) — wide
    enough that noise between two similar-scoring candidates shouldn't flip
    a switch, narrow enough that a real turn change (the same log shows an
    enormous real gap, e.g. 0.9 vs -1.0) clears it easily. Both need
    real-footage tuning on the next vast.ai run.
  - Accepted residual, documented rather than fixed: the window immediately
    after a switch has no minimum length of its own — only the *challenge*
    that produced it had to sustain the dwell. A second switch could in
    principle follow almost immediately. Watch for suspiciously dense
    switch clustering in the next real run's `speaker window: ...` log
    lines.
- **`_best_segment_by_mean_score(candidates, scores_by_seg, log)`**:
  preserves the pre-this-change selection logic exactly (pick the single
  segment with the highest mean score) — used as the timeline construction's
  own error fallback. Since scoring already happened once in
  `_score_candidates`, falling back here costs **zero additional Light-ASD
  inference**.
- **`select_speaker_timeline(video_path, tracks, source_fps, total_frames, logger)`**
  replaces `select_primary_track_with_asd` (retired, not kept in parallel —
  keeping it would either duplicate the scoring loop, doubling Light-ASD
  cost, or rot as dead code) at the pipeline's single call site
  (`src/reframe/service.py`). Absorbs every existing fallback tier from the
  two earlier fixes today (`ImportError` / no eligible segment / model-load
  failure → `_segment_heuristic_fallback`; now returns the whole
  `TrackSegment` rather than just its boxes, so its `track_id`/
  `segment_index` can populate the wrapping `SpeakerWindow` for logging,
  a small quality fix caught during this session's own verification) plus a
  new one: if `_build_speaker_windows` itself throws, falls back to
  `_best_segment_by_mean_score`. Every fallback tier wraps its result as one
  whole-clip `SpeakerWindow` — the worst case after this change is always
  **exactly** today's pre-existing behavior, never a new failure mode.
- **`src/reframe/crop_path.py`**: new `build_crop_path_from_windows(windows,
  source_width, source_height, total_frames, fps, smoothing_window)`
  generalizes `_interpolate_with_segment_breaks`'s existing pattern — each
  window is interpolated independently (including its own internal
  continuity-break detection, same defense-in-depth as before, since a
  window's boxes normally already come from one continuity-clean
  `TrackSegment` so this is usually a no-op) and masked into only that
  window's own `[start_frame, end_frame)` span, so a window's own
  edge-hold/extrapolation never bleeds into a neighboring window — a hard
  cut at every window boundary, not just the pre-existing intra-track
  discontinuity boundaries. The old `build_crop_path()` is now a **thin
  wrapper** around this (one whole-clip `SpeakerWindow`), kept rather than
  deleted so any caller still passing a flat box list keeps working
  unchanged — **confirmed byte-identical** to the pre-this-change
  `build_crop_path()` on the same fixture (see verification below).
- **`src/reframe/service.py`**: `run_reframe()` now calls
  `select_speaker_timeline()` + `build_crop_path_from_windows()`, with new
  logging: total window/switch count and, per window, its frame range,
  seconds, `track_id`, and `segment_index`.
- **New diagnostic logging** (cheap, no behavior change, added specifically
  to help judge the `track_id=3` ambiguity above from the next real run's
  log without another frame-extraction session): per candidate during
  scoring, `center_x_std` (position variance) alongside the existing
  `mean_score` — a segment spanning a long multi-speaker exchange with
  *unexpectedly low* `center_x_std` is suspicious evidence of a track
  quietly hovering between two people rather than genuinely tracking one
  stable speaker. Per finalized `SpeakerWindow`, its own `center_x_std` and
  `max_velocity_px_s` (reusing `track_continuity`'s existing velocity
  formula, purely as a logged number here, not a new split trigger).
- **Scope unchanged from every earlier reframe entry**: no changes to
  `src/highlights/` (including jump-cut `segments_json`), `src/rendering/clipper.py`,
  `src/tracking/`, `src/detection/`, or `track_continuity.py` (still used
  internally by `_build_segments`, same role as before) — reframe still
  operates entirely on the already-rendered, already-stitched clip file.

**Verified locally** (hand-built fixtures, no CUDA GPU, no real footage —
same discipline as the rest of this module; regression checks compare
against the actual pre-session `crop_path.py`/`speaker_selection.py` loaded
from git history, not a hand-reproduced approximation): `_score_frame_indices`
against a segment starting mid-clip and one starting at frame 0 with a
different fps, both matched manual arithmetic; a synthetic two-candidate
fixture (segment A high-then-low, segment B low-then-high, overlapping in
time) confirmed the timeline starts on A, does **not** switch at A's own
score drop (B hasn't cleared margin/dwell yet), and switches to B with the
window boundary backdated to exactly the frame B first cleared the margin —
this test caught a **real bug** during verification: the incumbent's
comparison score was frozen at its last *winning* moment instead of
tracking its actual current (already-declining) score, which silently
prevented switches indefinitely once an incumbent's peak was higher than a
later challenger's peak; fixed, then reverified. A brief above-margin blip
well under `dwell_frames` correctly triggers no switch (flicker rejection);
a coverage gap between two candidates correctly holds the incumbent and
doesn't spuriously switch to a same-scoring successor once it appears
(gap-hold). Fallback regression: with `LightASDScorer` import forced to fail,
`select_speaker_timeline`'s single-window result is **byte-identical** to
the pre-session `select_primary_track_with_asd()`'s result on the same
2-track fixture. `build_crop_path_from_windows` hard-cut behavior confirmed
on a 2-window fixture (position holds through the first window's last
frame, jumps cleanly at the boundary, no ramp). `build_crop_path()`'s
thin-wrapper output confirmed **byte-identical** to the pre-session
`build_crop_path()` on the same fixture, including the empty-boxes
freeze-center path. The 3+-person fragmentation fallback (`method=segment-heuristic`,
from the fix immediately above this one) re-verified through the new
`select_speaker_timeline` path, now correctly carrying `track_id` into the
wrapping `SpeakerWindow` (previously `None` there, a minor logging gap
fixed in the same pass). Full chain (`_detect_and_track` →
`select_speaker_timeline` → `build_crop_path_from_windows`) re-run
end-to-end against the existing synthetic zero-face test clip, confirmed a
single freeze-center window and the `method=none` log line. Diagnostic
logging helpers (`_center_x_std`, `_max_velocity_px_per_sec`) run without
crashing on both low- and high-variance fixtures.

**Not yet verified, needs a real vast.ai run** (same caveat category as the
rest of this module): whether real per-frame Light-ASD scores are stable/
discriminating enough in practice to avoid flicker on genuine speech (only
tested against clean hand-written score curves, never real model output
noise); whether `MIN_DWELL_SECONDS=1.5`/`SCORE_MARGIN=0.3` are anywhere
close to well-tuned; and — the question this fix was specifically built to
answer — whether this actually resolves clip #11's visually-confirmed
symptom, or whether the new `center_x_std`/`max_velocity_px_s` diagnostics
instead confirm `track_id=3` was a single track hovering between two people
at the detection/tracking level all along, a case no amount of
selection-level logic can fix (which would point to a future YOLOv8+ByteTrack
investigation, out of scope here).

**Light-ASD re-evaluated against newer ASD models, per explicit user request
after the crop-drift fix above — kept as-is, shelved not reopened
(2026-08-01).** User asked whether a stronger ASD model exists worth a small
production-cost increase. Researched current AVA-ActiveSpeaker benchmark
standings:
- **LR-ASD** (same author as Light-ASD, Springer IJCV 2025 — an explicit
  "extended version" of Light-ASD): 94.45% mAP vs. Light-ASD's 94.1%
  (+0.35pp), MIT-licensed, pretrained weights published at
  `github.com/Junhua-Liao/LR-ASD`.
- **LoCoNet** (CVPR 2023): 95.2% mAP (+1.1pp), but ~22.5M params / 2.6G FLOPs
  vs. Light-ASD's 1.0M / 0.6G (~22× heavier) — and its official repo
  (`github.com/SJTUwxz/LoCoNet_ASD`) has **no LICENSE file at all** (confirmed
  via the GitHub API, `license: null`), making it legally unusable to vendor
  without contacting the authors for explicit permission.
- **TalkNCE** (ICASSP 2024): 95.5% mAP (+1.4pp) — the TalkNCE repo itself is
  MIT, but it is LoCoNet trained with an added contrastive loss, not a
  standalone architecture, so it inherits both LoCoNet's heavier compute
  profile and effectively the same licensing blocker.
- **D²Stream** (arXiv, Dec 2025, newest claimed SOTA): 95.6% mAP, but no
  public code or weights found — too immature to depend on.
- The field is effectively at a plateau (94.1-95.6% mAP across all of the
  above) — no genuinely large accuracy jump is available at any price point
  right now, only single-digit-percentage-point gains.

**Decision: keep Light-ASD, fully shelved, not an open "revisit if X"
item.** Per the user's explicit choice among the options presented
(try LR-ASD / pursue LoCoNet-family despite the license gap / shelve
everything), the user picked shelving everything. Not reopened
autonomously — only on a future explicit user request, same treatment as
the custom-Pillow-caption-renderer and per-word-background-highlight
decisions above. If revisited later, LR-ASD is the lowest-effort path (MIT,
same-author codebase, small gain); LoCoNet/TalkNCE would need the license
question resolved with their authors first before any vendoring work starts.

**Colab notebook added for reframe/captioning re-runs without a fresh Claude
call (2026-08-02).** `notebooks/haroclip_colab.ipynb` — prompted by real
peak-VRAM numbers from a vast.ai run (`data/VAST/logs/887cd489-.../reframe.log`)
coming in far below the 24GB budget (reframe stage topped out ~1.3GB
`reserved`, per `log_vram()`, though that call only snapshots right after
each model load, not true inference-time peak — so this doesn't confirm the
whole pipeline's real ceiling, whisper's own load line showed an implausible
0.0MB for the same reason), making a much cheaper GPU tier worth exploring.
Google Colab was evaluated: free tier gives a T4 (16GB VRAM), and Colab Pro's
$11.99/mo for 100 compute units works out to roughly $0.14/hr of T4 time vs.
vast.ai's ~$0.35-0.55/hr for a 4090 — the trade-off is slower raw compute
(Turing T4 vs. Ampere/Ada 3090/4090) and no persistent server/session
between runs, not a fundamental blocker for this project's CLI-only current
path.

The notebook's actual purpose is narrower than "run the pipeline on Colab"
generically — it resumes a **specific already-processed job**
(`fa5be0db-ff05-4f3f-b3eb-eccc9af8915d`, tracked in `data/VAST/haroclip.db`)
whose highlight *selection* is already done (`HighlightClip.segments_json`
for all 12 clips already in the DB) purely to re-run **reframe** (to pick up
today's dynamic multi-speaker-follow feature) and **captioning**, without
spending Claude API credit on a selection that's already been paid for.
Discovered along the way: the DB alone isn't sufficient to resume — both
`run_reframe()`/`run_captioning()` only check DB status to short-circuit,
never on-disk file existence, and the actual input files
(`data/clips/<job_id>/clip_XX.mp4`, `data/videos/<job_id>/transcript.json`)
weren't present locally for this job (only `campaign_briefs/`/`captioned/`/
`captions/`/`logs/` had been downloaded from the vast.ai instance, which may
no longer be running). Rather than requiring the original instance, the
notebook regenerates those inputs itself: re-downloads the source video from
`IngestionJob.source_url` (`run_processing(..., force=True)` — yt-dlp +
ffmpeg, no GPU/LLM), re-transcribes via `transcribe()` directly (whisper,
GPU, **not** `run_highlight_detection()`, which would also re-call Claude
and delete the existing `HighlightClip` rows), and re-renders each clip via
`render_clip()` using the segment timestamps **already stored** in
`segments_json` — so the highlight selection itself is never touched or
re-derived, only its render artifacts. Assumes the source YouTube link is
still resolvable via yt-dlp (a real, flagged risk — Colab's shared IP ranges
are sometimes rate-limited/blocked by YouTube; noted in the notebook as a
manual-upload fallback, not automated).

Setup cells mirror `scripts/entrypoint.sh`'s steps (ffmpeg, Python deps,
YOLOv8-face weight download) adapted for a Drive-mounted `data/` tree instead
of a fresh vast.ai instance's local disk — `torch`/`torchvision` are
deliberately *not* reinstalled since Colab's runtime already ships a
CUDA-matched build, unlike vast.ai's PyTorch-template assumption.
**`YOLOV8_FACE_WEIGHTS_PATH` needed calling out explicitly**: it has its own
independent `os.getenv` default in `src/detection/face_detector.py`
("data/models/...", a plain relative-path literal), not derived from
`DATA_DIR`, so pointing `DATA_DIR` at Drive doesn't automatically relocate
it — the notebook sets both env vars explicitly. Also flags a real gotcha
confirmed by reading `src/utils/db.py`: `DATA_DIR` is read once at module
import time, not lazily, so the env var must be set before any `src` import
in the notebook or a mid-session fix requires a full runtime restart, not
just re-running the setting cell.

**Verification status**: every function call in the notebook was verified
against its actual current signature by reading the source directly
(`run_processing`, `transcribe`, `render_clip`, `run_reframe`,
`run_captioning`, all in `src/*/service.py` or equivalent) and the notebook
JSON was validated to parse correctly — but **the notebook itself has not
been run on a real Colab instance**, same "needs a real run" caveat as every
other unverified piece of this project. Open questions for that first run:
whether yt-dlp actually succeeds from Colab's IP, whether `pip install`
resolves cleanly against Colab's pre-installed torch build without version
conflicts, and — the actual point of doing this — whether the dynamic
multi-speaker-follow fix visibly changes the previously-frozen clip's crop
behavior once reframe re-runs.

**Second Colab notebook added for a full end-to-end run (2026-08-02)**,
per user request for something broader than the resume-only notebook above:
`notebooks/haroclip_colab_e2e.ipynb` replicates `src/pipeline/run.py`'s
exact orchestration (`--url` XOR `--job-id`, `--force`,
`--campaign-context`/`--campaign-file`/`--campaign-pdf`, `--hf-token`) as
notebook cells, for **either** a brand-new video **or** resuming an existing
`ingestion_job_id` (the requested "use case for continuing forward" —
every stage past ingestion is already idempotent, so resuming safely picks
up wherever a job actually left off, or `FORCE=True` redoes everything).
Unlike the first notebook, this one **does** call the Claude API for real
(fresh highlight detection) unless resuming a job whose highlights are
already `READY` — `ANTHROPIC_API_KEY` and `HF_TOKEN` are both entered via
`getpass` rather than sitting as plain variables in the notebook file
(`HF_TOKEN` started as a plain config-cell variable, moved to `getpass`
after user feedback that a credential shouldn't sit in cleartext in the
notebook even though it's optional — same reasoning already applied to
`ANTHROPIC_API_KEY`; Colab's built-in encrypted Secrets manager was offered
as a persist-across-sessions alternative but declined in favor of keeping
both keys on the same `getpass` pattern). `pipeline/run.py`'s `main()` itself was **not**
called directly — it's argparse-only and calls `sys.exit(1)` on every
failure path, which would kill a Colab kernel — so its ~15-line
orchestration body was replicated as separate cells using plain
`raise RuntimeError(...)` instead, giving per-stage visibility in Colab's
output rather than one opaque process exit. Setup cells (Drive mount, env
vars, system/Python deps, `sys.path`, YOLOv8-face weights) are copied
identically from the first notebook, not redesigned. Same verification
status as the first: every function call (`create_job`, `run_validation`,
`apply_campaign_brief`, `run_processing`, `run_highlight_detection`,
`run_reframe`, `run_captioning`) was checked against its actual current
signature by reading the source directly, and the notebook JSON parses
correctly — but it has **not been run on a real Colab instance yet**.

**First real Colab run surfaced two bugs in `haroclip_colab_e2e.ipynb`, both
fixed (2026-08-02):**
1. **`init_db()` created zero tables** (`OperationalError: no such table:
   ingestion_jobs` on the very first DB write). Root cause:
   `Base.metadata.create_all()` only creates tables for SQLAlchemy models
   that have actually been imported/registered with `Base` by the time it
   runs, and the "Open the DB" cell called `init_db()` before any cell had
   imported `IngestionJob`/`ProcessingJob`/etc. `src/pipeline/run.py` never
   hits this because it imports every service module (which transitively
   import their models) at the top of the file, before `main()` ever calls
   `init_db()` — the notebook's cell-by-cell structure broke that implicit
   ordering. Fixed by importing all 5 model modules
   (`IngestionJob`/`ProcessingJob`/`HighlightJob`/`HighlightClip`/
   `ReframeJob`/`CaptionJob`) directly in the "Open the DB" cell, before
   `init_db()` runs there. `haroclip_colab.ipynb` doesn't have this bug — it
   always operates on an existing DB copy that already has every table from
   its original vast.ai run, so `create_all()`'s completeness never mattered
   there.
2. **`ctranslate2` (faster-whisper's backend) CUDA/cuDNN version
   incompatibility loading the whisper model — turned out to be a moving
   target, not a single fixed pin.** Three rounds on the real Colab session:
   - **Round 1**: default `pip install faster-whisper` pulled in
     `ctranslate2>=4.5.0`, which needs CuDNN v9/CUDA>=12.3 — failed with
     `RuntimeError: CUDA failed with error CUDA driver version is
     insufficient for CUDA runtime version` (driver too old for that CUDA
     runtime). Fixed (at the time) by pinning `ctranslate2==4.4.0`.
   - **Round 2**: that pin then failed differently —
     `Could not load library libcudnn_ops_infer.so.8` — because `4.4.0`
     needs cuDNN8, but this Colab environment's installed cuDNN was v9 (a
     driver-too-old-for-newest/cuDNN-too-new-for-oldest sandwich). Tried
     `ctranslate2==4.5.0` as a middle ground (the version that added cuDNN9
     support without yet requiring the newest CUDA runtime).
   - **Round 3**: before confirming whether 4.5.0 worked, `!nvidia-smi` on a
     later session showed driver `580.82.07` / **CUDA 13.0** — comfortably
     new enough for any recent `ctranslate2`, contradicting the Round 1
     "insufficient driver" diagnosis entirely. This confirmed **Colab
     assigns different GPU host machines with different driver/CUDA/cuDNN
     bundles across sessions** — there is no single version pin that's
     reliably correct across runs. (Google Colab's Command Palette → "Use
     fallback runtime version" — which reverts the whole CUDA/driver/cuDNN
     image to the pre-upgrade snapshot — was discussed as a possible
     environment-level fix instead of chasing package pins, but it doesn't
     persist across sessions and its availability window is temporary, so it
     isn't a substitute for the notebook handling version drift on its own.)
   - **Final approach**: both notebooks now install `ctranslate2` **unpinned**
     (whatever `faster-whisper` resolves by default) and rely on a
     troubleshooting cell instead of a fixed version — the cell maps the
     *exact error text* to a direction to pin (`"...insufficient for CUDA
     runtime..."` → pin older, `4.4.0`; `"...libcudnn_ops_infer.so.8..."` →
     pin newer, `4.5.0`) plus `!nvidia-smi`/`!pip show ctranslate2`
     diagnostics to check first, plus a CPU-fallback (`WHISPER_DEVICE=cpu`)
     escape hatch as a last resort. This is a **live external
     version-compatibility constraint that varies by session**, not a
     HaroCLIP code bug and not a one-time-fixable one — expect to actually
     need the troubleshooting cell on some fraction of real runs, not just
     read it as a footnote.

## Architecture

Planned across 5 phases (details TBD as implementation proceeds).

## Next steps (not yet started — waiting on direction)

**A real vast.ai run already happened (2026-07-25, 57-min video, RTX 3090-class,
`vast-ai-e2e-prep` branch)** — all four stages ran for real end-to-end, but it
surfaced the context-window bug described above (highlight-detection LLM stage). Fixed
by switching to the Claude API (2026-07-26) plus quality upgrades (whisper `float16`,
YOLOv8-face `medium`, later `xlarge`) using the VRAM the local LLM no longer needs.
**Captioning, the further YOLOv8-face `xlarge`/`imgsz=1280`/whisper `vad_filter`
pass (also 2026-07-26), jump-cut clip support, the karaoke-caption/reframe-encode
rewrite, and the clip-boundary snapping/duration-cap fix (all 2026-08-01) are new
since that run and have never been exercised on vast.ai at all.** None of this is yet
re-verified with a real run — that's next:

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
   real benefit; confirm Claude's jump-cut choices on real content actually respect
   the "same idea only, not a compilation of different hooks" rule (only locally
   verified for validation/rendering/caption-timing correctness so far, never for
   actual model judgment quality) and that the resulting hard cuts feel intentional
   rather than jarring when watched back; confirm the karaoke word-highlight/numeral
   accent actually reads well against real speech cadence (not hand-written fake
   timestamps) and that `MarginV=260` actually clears the real TikTok/Reels/Shorts
   apps' UI when viewed on an actual phone, not just the raw frame edge; confirm
   `CROP_CRF=17`'s real encode-time cost on production-length clips fits comfortably
   under the reframe stage's new 600s timeout on the rented GPU instance's CPU;
   confirm the new word-boundary snapping (`snap_candidates` in
   `src/highlights/llm.py`) actually lands clips/captions on clean word edges against
   real transcript timing noise (only verified against hand-written fixtures so far),
   that `SNAP_WINDOW_SECONDS=2.0` is wide enough in practice (watch for repeated
   `snap: no word ... within 2.0s` warnings in the logs — a sign the window needs
   widening), and that clips using the new 61-75s stretch room actually read as
   complete thoughts rather than padded; confirm the 2026-08-01 caption font
   overhaul (`FontSize=90`, `FontName=Rubik Bold` loaded via `fontsdir` from
   `src/captioning/fonts/`, `WrapStyle=0`) actually resolves correctly on the
   rented instance's Linux/fontconfig libass backend the same way it did on
   this dev machine's Windows/DirectWrite backend (real-render-verified there,
   not yet confirmed on Linux at all) — watch the ffmpeg log for a
   `fontselect: ... -> Rubik-Bold` line same as the one captured locally; a
   fallback to some other font here would mean `fontsdir`/the vendored
   `Rubik-Bold-static.ttf` isn't being picked up as expected; confirm the
   2026-08-01 crop-drift fix (`src/reframe/track_continuity.py`, the
   segment-aware `build_crop_path()`, and per-segment ASD selection in
   `speaker_selection.py`) actually keeps the crop locked onto one person
   when two people are close together in real footage, and whether
   `MAX_JUMP_PX_PER_SEC=1800.0` needs retuning (watch for either missed
   switches — crop still drifting between two people — or over-triggering on
   fast legitimate head motion, logged via the "Light-ASD scores per track
   segment" / "method=heuristic" log lines); confirm the 2026-08-01
   crop-freeze/center-lock fix (the new `_segment_heuristic_fallback` tier and
   `FaceTracker(minimum_consecutive_frames=2)` in `speaker_selection.py`/
   `face_tracker.py`) actually stops the dead-center freeze on real
   3+-person podcast footage — watch for `method=segment-heuristic` and the
   `segment summary: ... eligible for ASD ...` log lines to confirm how often
   the ASD-eligible floor is actually being missed in practice, and whether
   the area-based segment pick (no active-speaker signal) still produces a
   visually reasonable crop compared to a real Light-ASD-scored segment;
   confirm the 2026-08-01 dynamic multi-speaker-follow feature
   (`SpeakerWindow`/`select_speaker_timeline` in `speaker_selection.py`,
   `build_crop_path_from_windows` in `crop_path.py`) actually fixes clip
   #11's frozen-between-two-people symptom (the exact clip whose visual
   inspection motivated this feature) — watch for `speaker window: ...` log
   lines showing multiple windows/switches on real multi-speaker clips
   (versus collapsing back to one window every time, which would suggest
   the hysteresis constants are too conservative or Light-ASD's real
   per-frame scores are too noisy to discriminate); cross-check the new
   `center_x_std`/`max_velocity_px_s` diagnostics against clip #11's
   specific track/segment to determine whether it was ever fixable at the
   selection level at all, versus being a detection/tracking-level
   single-track-hovering-between-two-faces issue; and watch for whether
   `MIN_DWELL_SECONDS=1.5`/`SCORE_MARGIN=0.3` need retuning (dense switch
   clustering suggests too loose, no switches at all on an obviously
   multi-speaker clip suggests too strict).
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
