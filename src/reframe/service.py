import time

from sqlalchemy.orm import Session

from src.detection.face_detector import FaceDetector
from src.highlights.models import HighlightClip, HighlightJob
from src.reframe.crop_path import build_crop_path, compute_crop_width
from src.reframe.enums import ReframeStatus
from src.reframe.exceptions import ReframeError
from src.reframe.models import ReframeJob
from src.reframe.renderer import render_reframed_clip
from src.reframe.speaker_selection import select_primary_track_with_asd
from src.reframe.storage import reframe_output_path
from src.tracking.face_tracker import FaceTracker
from src.utils.db import DATA_DIR
from src.utils.logging import get_job_logger

SAMPLE_FPS = 5


def get_or_create_reframe_job(db: Session, highlight_clip_id: str) -> ReframeJob:
    job = (
        db.query(ReframeJob)
        .filter_by(highlight_clip_id=highlight_clip_id)
        .one_or_none()
    )
    if job is None:
        job = ReframeJob(highlight_clip_id=highlight_clip_id)
        db.add(job)
        db.commit()
        db.refresh(job)
    return job


def _detect_and_track(video_path, logger=None):
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ReframeError(f"could not open clip: {video_path}", stage="detecting")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    sample_interval = max(1, round(fps / SAMPLE_FPS))

    if logger:
        logger.info(
            "clip video: %dx%d, %d frames @ %.1ffps, sampling every %d frames (~%.1ffps)",
            width, height, total_frames, fps, sample_interval, fps / sample_interval,
        )

    all_tracks = []
    sampled_frame_count = 0
    total_detections = 0
    detector = FaceDetector(logger=logger)
    tracker = FaceTracker()
    try:
        frame_index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_index % sample_interval == 0:
                sampled_frame_count += 1
                boxes = detector.detect(frame, frame_index)
                total_detections += len(boxes)
                all_tracks.extend(tracker.update(frame_index, boxes))
            frame_index += 1
    finally:
        cap.release()
        detector.close()

    if logger:
        unique_tracks = len({t.track_id for t in all_tracks})
        logger.info(
            "detection+tracking done: %d frames sampled, %d total face detections, %d unique tracks",
            sampled_frame_count, total_detections, unique_tracks,
        )

    return all_tracks, width, height, total_frames, fps


def run_reframe(db: Session, highlight_clip_id: str, force: bool = False) -> ReframeJob:
    clip = db.get(HighlightClip, highlight_clip_id)
    if clip is None:
        raise ValueError(f"highlight clip not found: {highlight_clip_id}")

    highlight_job = db.get(HighlightJob, clip.highlight_job_id)
    ingestion_job_id = highlight_job.ingestion_job_id if highlight_job else highlight_clip_id
    logger = get_job_logger("reframe", ingestion_job_id)
    logger.info("=== reframing clip %s (rank #%d, [%.1f-%.1f]) ===", clip.id, clip.rank, clip.start_seconds, clip.end_seconds)

    job = get_or_create_reframe_job(db, highlight_clip_id)

    if job.status == ReframeStatus.READY and not force:
        logger.info("already ready, short-circuiting (use --force to redo)")
        return job

    try:
        video_path = DATA_DIR / clip.output_path

        job.status = ReframeStatus.DETECTING
        job.error_stage = None
        job.error_message = None
        db.commit()

        all_tracks, width, height, total_frames, fps = _detect_and_track(video_path, logger=logger)

        job.status = ReframeStatus.TRACKING
        db.commit()

        job.status = ReframeStatus.CROPPING
        db.commit()

        primary_track_id = select_primary_track_with_asd(video_path, all_tracks, fps, logger=logger)
        primary_boxes = [t for t in all_tracks if t.track_id == primary_track_id]
        crop_w = compute_crop_width(width, height)
        crop_path = build_crop_path(primary_boxes, width, height, total_frames)
        if crop_path:
            xs = [x for x, _y in crop_path]
            logger.info(
                "crop path: primary_track_id=%s pan range x=[%d, %d] (width=%d, crop_w=%d)",
                primary_track_id, min(xs), max(xs), width, crop_w,
            )

        job.status = ReframeStatus.RENDERING
        db.commit()

        out_path = reframe_output_path(highlight_clip_id)
        render_start = time.monotonic()
        render_reframed_clip(video_path, crop_path, crop_w, out_path)
        render_duration = time.monotonic() - render_start
        output_size_mb = out_path.stat().st_size / (1024**2)
        logger.info("reframe rendered in %.1fs: %s (%.1fMB)", render_duration, out_path.name, output_size_mb)

        job.output_path = str(out_path.relative_to(DATA_DIR))
        job.status = ReframeStatus.READY
        db.commit()

    except ReframeError as e:
        job.status = ReframeStatus.FAILED
        job.error_stage = e.stage
        job.error_message = e.message
        db.commit()
        logger.exception("reframe failed at stage=%s", e.stage)
    except Exception as e:
        job.status = ReframeStatus.FAILED
        job.error_stage = "unknown"
        job.error_message = str(e)
        db.commit()
        logger.exception("reframe failed with unexpected error")

    db.refresh(job)
    return job
