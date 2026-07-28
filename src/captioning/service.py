import subprocess
import time
from pathlib import Path

from sqlalchemy.orm import Session

from src.captioning.enums import CaptionStatus
from src.captioning.exceptions import CaptioningError
from src.captioning.models import CaptionJob
from src.captioning.storage import caption_srt_path, captioned_output_path
from src.captioning.subtitles import (
    build_burst_cues,
    load_transcript,
    slice_words_to_clip,
    write_srt,
)
from src.highlights.models import HighlightClip, HighlightJob
from src.reframe.enums import ReframeStatus
from src.reframe.models import ReframeJob
from src.reframe.renderer import OUTPUT_HEIGHT, OUTPUT_WIDTH
from src.utils.db import DATA_DIR
from src.utils.logging import get_job_logger

FFMPEG_TIMEOUT_SECONDS = 120
# Bottom-center burst captions: white text, black outline, no background box —
# standard short-form-video look. Relies on the ffmpeg build having libass (the
# `subtitles` filter) compiled in — see docs/hardware-spec.md / VAST_GUIDE.md.
# FontSize=36 (not the original 14, far too small on a 1080-wide vertical frame —
# but 72 was tried first and, verified visually against real footage, was FAR too
# large, covering nearly half the frame; 36 is a real, image-verified middle
# ground, not a formula-derived guess). FontName names the bold weight directly
# (sidesteps ASS Bold-flag parsing ambiguity) — real font must be installed
# system-side, see Dockerfile/VAST_GUIDE.md/scripts/entrypoint.sh
# (fonts-dejavu-core). Outline bumped modestly to match the larger font.
SUBTITLE_STYLE = (
    "FontName=DejaVu Sans Bold,FontSize=36,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000,BorderStyle=1,Outline=3,Alignment=2"
)


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
        words = slice_words_to_clip(segments, clip.start_seconds, clip.end_seconds)
        cues = build_burst_cues(words)
        srt_path = caption_srt_path(highlight_clip_id)
        write_srt(cues, srt_path)
        logger.info(
            "captions generated: %d words in clip range -> %d cues -> %s",
            len(words), len(cues), srt_path.name,
        )

        job.srt_path = str(srt_path.relative_to(DATA_DIR))
        job.status = CaptionStatus.RENDERING
        db.commit()

        reframed_path = DATA_DIR / reframe_job.output_path
        out_path = captioned_output_path(highlight_clip_id)
        render_start = time.monotonic()
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", str(reframed_path),
                    "-vf",
                    f"subtitles='{_escape_subtitles_path(srt_path)}'"
                    f":original_size={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}"
                    f":force_style='{SUBTITLE_STYLE}'",
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
