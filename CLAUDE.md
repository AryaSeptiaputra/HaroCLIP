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
  get 5-10 ranked candidate segments as
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
   complete thoughts rather than padded.
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
