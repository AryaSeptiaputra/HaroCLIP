import time

from sqlalchemy.orm import Session

from src.ingestion.enums import JobStatus, LinkType
from src.ingestion.models import IngestionJob
from src.processing.audio_extractor import extract_audio
from src.processing.downloader import download_direct_video, download_platform_video
from src.processing.enums import ProcessingStatus
from src.processing.exceptions import ProcessingError
from src.processing.models import ProcessingJob
from src.processing.storage import job_dir
from src.utils.db import DATA_DIR
from src.utils.logging import get_job_logger


def get_or_create_processing_job(db: Session, ingestion_job_id: str) -> ProcessingJob:
    job = (
        db.query(ProcessingJob)
        .filter_by(ingestion_job_id=ingestion_job_id)
        .one_or_none()
    )
    if job is None:
        job = ProcessingJob(ingestion_job_id=ingestion_job_id)
        db.add(job)
        db.commit()
        db.refresh(job)
    return job


def run_processing(db: Session, ingestion_job_id: str, force: bool = False) -> ProcessingJob:
    logger = get_job_logger("processing", ingestion_job_id)

    ingestion_job = db.get(IngestionJob, ingestion_job_id)
    if ingestion_job is None:
        raise ValueError(f"ingestion job not found: {ingestion_job_id}")
    if ingestion_job.status != JobStatus.READY:
        raise ValueError(
            f"ingestion job {ingestion_job_id} is not ready (status={ingestion_job.status.value})"
        )

    job = get_or_create_processing_job(db, ingestion_job_id)

    if job.status == ProcessingStatus.READY and not force:
        logger.info("already ready, short-circuiting (use --force to redo)")
        return job

    try:
        dest_dir = job_dir(ingestion_job_id)

        job.status = ProcessingStatus.DOWNLOADING
        job.error_stage = None
        job.error_message = None
        db.commit()

        logger.info("downloading via %s: %s", ingestion_job.link_type.value, ingestion_job.source_url)
        download_start = time.monotonic()
        if ingestion_job.link_type == LinkType.PLATFORM:
            video_path, platform_metadata = download_platform_video(ingestion_job.source_url, dest_dir)
            job.description = platform_metadata.description
            job.upload_date = platform_metadata.upload_date
            job.uploader = platform_metadata.uploader
            job.platform = platform_metadata.platform
        else:
            video_path = download_direct_video(ingestion_job.source_url, dest_dir)
        download_duration = time.monotonic() - download_start
        video_size_mb = video_path.stat().st_size / (1024**2)
        logger.info(
            "download done in %.1fs: %s (%.1fMB)", download_duration, video_path.name, video_size_mb
        )

        job.video_path = str(video_path.relative_to(DATA_DIR))
        db.commit()

        job.status = ProcessingStatus.EXTRACTING_AUDIO
        db.commit()

        audio_path = dest_dir / "audio.wav"
        extract_start = time.monotonic()
        extract_audio(video_path, audio_path)
        extract_duration = time.monotonic() - extract_start
        audio_size_mb = audio_path.stat().st_size / (1024**2)
        logger.info(
            "audio extraction done in %.1fs: %s (%.1fMB)", extract_duration, audio_path.name, audio_size_mb
        )

        job.audio_path = str(audio_path.relative_to(DATA_DIR))
        job.status = ProcessingStatus.READY
        db.commit()
        logger.info("processing ready: video=%s audio=%s", job.video_path, job.audio_path)

    except ProcessingError as e:
        job.status = ProcessingStatus.FAILED
        job.error_stage = e.stage
        job.error_message = e.message
        db.commit()
        logger.exception("processing failed at stage=%s", e.stage)
    except Exception as e:
        job.status = ProcessingStatus.FAILED
        job.error_stage = "unknown"
        job.error_message = str(e)
        db.commit()
        logger.exception("processing failed with unexpected error")

    db.refresh(job)
    return job
