import json
import time

from sqlalchemy.orm import Session

from src.highlights.enums import HighlightStatus
from src.highlights.exceptions import HighlightError
from src.highlights.llm import generate_candidates, parse_candidates
from src.highlights.models import HighlightClip, HighlightJob
from src.highlights.storage import clip_job_dir
from src.ingestion.models import IngestionJob
from src.processing.enums import ProcessingStatus
from src.processing.models import ProcessingJob
from src.rendering.clipper import render_clip
from src.rendering.exceptions import RenderingError
from src.transcription.transcriber import transcribe
from src.utils.db import DATA_DIR
from src.utils.logging import get_job_logger


def get_or_create_highlight_job(db: Session, ingestion_job_id: str) -> HighlightJob:
    job = (
        db.query(HighlightJob)
        .filter_by(ingestion_job_id=ingestion_job_id)
        .one_or_none()
    )
    if job is None:
        job = HighlightJob(ingestion_job_id=ingestion_job_id)
        db.add(job)
        db.commit()
        db.refresh(job)
    return job


def run_highlight_detection(
    db: Session, ingestion_job_id: str, force: bool = False
) -> HighlightJob:
    logger = get_job_logger("highlights", ingestion_job_id)

    ingestion_job = db.get(IngestionJob, ingestion_job_id)
    if ingestion_job is None:
        raise ValueError(f"ingestion job not found: {ingestion_job_id}")

    processing_job = (
        db.query(ProcessingJob).filter_by(ingestion_job_id=ingestion_job_id).one_or_none()
    )
    if processing_job is None or processing_job.status != ProcessingStatus.READY:
        status = processing_job.status.value if processing_job else "not started"
        raise ValueError(
            f"processing job for {ingestion_job_id} is not ready (status={status})"
        )

    job = get_or_create_highlight_job(db, ingestion_job_id)

    if job.status == HighlightStatus.READY and not force:
        logger.info("already ready, short-circuiting (use --force to redo)")
        return job

    try:
        video_path = DATA_DIR / processing_job.video_path
        audio_path = DATA_DIR / processing_job.audio_path
        video_duration = ingestion_job.duration_seconds
        if not video_duration:
            raise HighlightError(
                "ingestion job has no known video duration", stage="detection"
            )

        job.status = HighlightStatus.TRANSCRIBING
        job.error_stage = None
        job.error_message = None
        db.commit()

        segments = transcribe(audio_path, logger=logger)
        transcript_text = " ".join(s.text for s in segments)
        logger.info("full transcript (%d chars):\n%s", len(transcript_text), transcript_text)

        transcript_path = video_path.parent / "transcript.json"
        transcript_path.write_text(
            json.dumps([s.__dict__ for s in segments]), encoding="utf-8"
        )
        job.transcript_path = str(transcript_path.relative_to(DATA_DIR))
        db.commit()

        job.status = HighlightStatus.DETECTING
        db.commit()

        raw_response = generate_candidates(segments, logger=logger)
        candidates = parse_candidates(raw_response, video_duration, logger=logger)

        db.query(HighlightClip).filter_by(highlight_job_id=job.id).delete()
        db.commit()

        job.status = HighlightStatus.RENDERING
        db.commit()

        dest_dir = clip_job_dir(ingestion_job_id)
        for rank, candidate in enumerate(candidates, start=1):
            out_path = dest_dir / f"clip_{rank:02d}.mp4"
            render_start = time.monotonic()
            render_clip(video_path, candidate["start"], candidate["end"], out_path)
            logger.info(
                "clip #%d rendered in %.1fs: [%.1f-%.1f] -> %s",
                rank, time.monotonic() - render_start, candidate["start"], candidate["end"], out_path.name,
            )
            db.add(
                HighlightClip(
                    highlight_job_id=job.id,
                    rank=rank,
                    start_seconds=candidate["start"],
                    end_seconds=candidate["end"],
                    reason=candidate["reason"],
                    output_path=str(out_path.relative_to(DATA_DIR)),
                )
            )
        db.commit()

        job.status = HighlightStatus.READY
        db.commit()
        logger.info("highlight detection ready: %d clips", len(candidates))

    except (HighlightError, RenderingError) as e:
        job.status = HighlightStatus.FAILED
        job.error_stage = e.stage
        job.error_message = e.message
        db.commit()
        logger.exception("highlight detection failed at stage=%s", e.stage)
    except Exception as e:
        job.status = HighlightStatus.FAILED
        job.error_stage = "unknown"
        job.error_message = str(e)
        db.commit()
        logger.exception("highlight detection failed with unexpected error")

    db.refresh(job)
    return job
