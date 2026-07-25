import time

from sqlalchemy.orm import Session

from src.ingestion.enums import JobStatus, LinkType
from src.ingestion.exceptions import IngestionValidationError
from src.ingestion.ffprobe_validator import validate_direct_url
from src.ingestion.link_detection import detect_link_type
from src.ingestion.metadata import VideoMetadata
from src.ingestion.models import IngestionJob
from src.ingestion.ytdlp_validator import validate_platform_url
from src.utils.db import SessionLocal
from src.utils.logging import get_job_logger


def create_job(db: Session, source_url: str) -> IngestionJob:
    link_type = detect_link_type(source_url)
    job = IngestionJob(
        source_url=source_url,
        link_type=link_type,
        status=JobStatus.VALIDATING,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    logger = get_job_logger("ingestion", job.id)
    logger.info("created job %s: source_url=%s link_type=%s", job.id, source_url, link_type.value)
    return job


def _apply_metadata(job: IngestionJob, metadata: VideoMetadata) -> None:
    job.duration_seconds = metadata.duration_seconds
    job.width = metadata.width
    job.height = metadata.height
    job.video_codec = metadata.video_codec
    job.audio_codec = metadata.audio_codec
    job.title = metadata.title
    job.raw_metadata = metadata.raw
    job.status = JobStatus.READY


def run_validation(job_id: str) -> None:
    db = SessionLocal()
    logger = get_job_logger("ingestion", job_id)
    try:
        job = db.get(IngestionJob, job_id)
        if job is None:
            logger.warning("run_validation called for unknown job_id %s", job_id)
            return

        logger.info("validating source_url=%s link_type=%s", job.source_url, job.link_type)
        start = time.monotonic()
        try:
            if job.link_type == LinkType.PLATFORM:
                metadata = validate_platform_url(job.source_url)
            else:
                metadata = validate_direct_url(job.source_url)
            _apply_metadata(job, metadata)
            logger.info(
                "validation OK in %.1fs: duration=%.1fs %sx%s video_codec=%s audio_codec=%s title=%r",
                time.monotonic() - start,
                metadata.duration_seconds or -1,
                metadata.width,
                metadata.height,
                metadata.video_codec,
                metadata.audio_codec,
                metadata.title,
            )
        except IngestionValidationError as e:
            job.status = JobStatus.FAILED
            job.error_stage = e.stage
            job.error_message = e.message
            logger.exception("validation failed after %.1fs at stage=%s", time.monotonic() - start, e.stage)
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_stage = "validation"
            job.error_message = str(e)
            logger.exception("validation failed after %.1fs with unexpected error", time.monotonic() - start)

        db.commit()
    finally:
        db.close()
