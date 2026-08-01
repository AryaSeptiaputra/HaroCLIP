import json
import subprocess
import time
from pathlib import Path

from sqlalchemy.orm import Session

from src.captioning.enums import CaptionStatus
from src.captioning.exceptions import CaptioningError
from src.captioning.models import CaptionJob
from src.captioning.storage import caption_ass_path, captioned_output_path
from src.captioning.subtitles import (
    build_burst_cues,
    load_transcript,
    slice_words_to_clip,
    write_ass,
)
from src.highlights.models import HighlightClip, HighlightJob
from src.ingestion.models import IngestionJob
from src.reframe.enums import ReframeStatus
from src.reframe.models import ReframeJob
from src.reframe.renderer import OUTPUT_HEIGHT, OUTPUT_WIDTH
from src.utils.db import DATA_DIR
from src.utils.logging import get_job_logger

# 600 (not 120): same reasoning as src/rendering/clipper.py — this is also a
# CPU-bound libx264 re-encode (plus -crf 18, slower than default), confirmed to
# risk exceeding 120s on a high-resolution source. reframe/renderer.py's ffmpeg
# call is now also a real libx264 encode (not a stream copy) as of the
# 2026-08-01 mp4v-bottleneck fix, so it carries its own explicit timeout — see
# src/reframe/renderer.py's FFMPEG_TIMEOUT_SECONDS.
FFMPEG_TIMEOUT_SECONDS = 600
# Caption style (font, size, colors, karaoke/emphasis, safe-zone margin) now
# lives entirely in the generated .ass file's own [V4+ Styles] section — see
# src/captioning/subtitles.py's ASS_TEMPLATE — rather than as a force_style
# override here, since libass reads that section directly from the .ass input.

# src/captioning/fonts/ holds Rubik-Bold-static.ttf (see that directory's
# NOTICE.md for provenance) — passed to ffmpeg's subtitles filter as
# `fontsdir` so libass loads the font directly from this file, with no OS-
# level font installation step needed at all (no apt package, no Dockerfile/
# entrypoint.sh prerequisite — this replaced the old fonts-dejavu-core setup
# on 2026-08-01). Real-render-verified: without fontsdir, "Rubik Bold" isn't
# a name any font matcher can resolve on its own.
FONTS_DIR = Path(__file__).resolve().parent / "fonts"


def get_or_create_caption_job(db: Session, highlight_clip_id: str) -> CaptionJob:
    job = (
        db.query(CaptionJob)
        .filter_by(highlight_clip_id=highlight_clip_id)
        .one_or_none()
    )
    if job is None:
        job = CaptionJob(highlight_clip_id=highlight_clip_id)
        db.add(job)
        db.commit()
        db.refresh(job)
    return job


def _escape_subtitles_path(path: Path) -> str:
    # ffmpeg's subtitles filter splits its argument on ':' — escape the colon in
    # Windows drive letters (and normalize to forward slashes) so the path isn't
    # misparsed as filter options. No-op on POSIX paths (no ':' to escape).
    return str(path).replace("\\", "/").replace(":", "\\:")


def run_captioning(db: Session, highlight_clip_id: str, force: bool = False) -> CaptionJob:
    clip = db.get(HighlightClip, highlight_clip_id)
    if clip is None:
        raise ValueError(f"highlight clip not found: {highlight_clip_id}")

    highlight_job = db.get(HighlightJob, clip.highlight_job_id)
    if highlight_job is None:
        raise ValueError(f"highlight job not found for clip: {highlight_clip_id}")
    ingestion_job_id = highlight_job.ingestion_job_id
    ingestion_job = db.get(IngestionJob, ingestion_job_id)
    video_title = ingestion_job.title if ingestion_job else None

    reframe_job = (
        db.query(ReframeJob).filter_by(highlight_clip_id=highlight_clip_id).one_or_none()
    )
    if reframe_job is None or reframe_job.status != ReframeStatus.READY:
        status = reframe_job.status.value if reframe_job else "not started"
        raise ValueError(
            f"reframe job for clip {highlight_clip_id} is not ready (status={status})"
        )

    logger = get_job_logger("captioning", ingestion_job_id)
    logger.info(
        "=== captioning clip %s (rank #%d, [%.1f-%.1f]) ===",
        clip.id, clip.rank, clip.start_seconds, clip.end_seconds,
    )

    job = get_or_create_caption_job(db, highlight_clip_id)

    if job.status == CaptionStatus.READY and not force:
        logger.info("already ready, short-circuiting (use --force to redo)")
        return job

    try:
        if not highlight_job.transcript_path:
            raise CaptioningError(
                "highlight job has no transcript_path", stage="generating"
            )

        job.status = CaptionStatus.GENERATING
        job.error_stage = None
        job.error_message = None
        db.commit()

        transcript_path = DATA_DIR / highlight_job.transcript_path
        segments = load_transcript(transcript_path)
        clip_segments = [(s["start"], s["end"]) for s in json.loads(clip.segments_json)]
        words = slice_words_to_clip(segments, clip_segments)
        cues = build_burst_cues(words)
        ass_path = caption_ass_path(ingestion_job_id, highlight_clip_id)
        write_ass(cues, ass_path, OUTPUT_WIDTH, OUTPUT_HEIGHT)
        logger.info(
            "captions generated: %d words in clip range -> %d cues -> %s",
            len(words), len(cues), ass_path.name,
        )

        job.ass_path = str(ass_path.relative_to(DATA_DIR))
        job.status = CaptionStatus.RENDERING
        db.commit()

        reframed_path = DATA_DIR / reframe_job.output_path
        out_path = captioned_output_path(ingestion_job_id, video_title, clip.rank)
        render_start = time.monotonic()
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", str(reframed_path),
                    "-vf",
                    f"subtitles='{_escape_subtitles_path(ass_path)}'"
                    f":fontsdir='{_escape_subtitles_path(FONTS_DIR)}'",
                    # Final deliverable is always forced to 1080x1920 @ 120fps here,
                    # regardless of what the upstream reframe stage produced (belt-
                    # and-suspenders on top of OUTPUT_WIDTH/OUTPUT_HEIGHT already
                    # being 1080x1920). -r 120 is plain CFR frame duplication, not
                    # motion interpolation (minterpolate) — source footage is never
                    # actually shot at 120fps, this just re-tags/duplicates frames
                    # to hit the requested delivery framerate cheaply.
                    "-s", "1080x1920",
                    "-r", "120",
                    "-c:v", "libx264",
                    # crf 18 (not left at ffmpeg's default 23): this is the final
                    # deliverable pass and the one ffmpeg call in the project that
                    # burns compact high-contrast text glyphs, which show default
                    # compression artifacts more than general video content does.
                    "-crf", "18",
                    "-c:a", "copy",
                    str(out_path),
                ],
                capture_output=True,
                text=True,
                timeout=FFMPEG_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise CaptioningError(
                f"ffmpeg caption burn-in timed out after {FFMPEG_TIMEOUT_SECONDS}s",
                stage="rendering",
            )

        if result.returncode != 0:
            raise CaptioningError(
                f"ffmpeg caption burn-in failed: {result.stderr.strip()}", stage="rendering"
            )

        render_duration = time.monotonic() - render_start
        output_size_mb = out_path.stat().st_size / (1024**2)
        logger.info(
            "captions burned in %.1fs: %s (%.1fMB)",
            render_duration, out_path.name, output_size_mb,
        )

        job.output_path = str(out_path.relative_to(DATA_DIR))
        job.status = CaptionStatus.READY
        db.commit()

    except CaptioningError as e:
        job.status = CaptionStatus.FAILED
        job.error_stage = e.stage
        job.error_message = e.message
        db.commit()
        logger.exception("captioning failed at stage=%s", e.stage)
    except Exception as e:
        job.status = CaptionStatus.FAILED
        job.error_stage = "unknown"
        job.error_message = str(e)
        db.commit()
        logger.exception("captioning failed with unexpected error")

    db.refresh(job)
    return job
