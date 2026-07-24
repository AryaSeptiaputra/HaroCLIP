from sqlalchemy.orm import Session

from src.detection.face_detector import FaceDetector
from src.highlights.models import HighlightClip
from src.reframe.crop_path import build_crop_path, compute_crop_width
from src.reframe.enums import ReframeStatus
from src.reframe.exceptions import ReframeError
from src.reframe.models import ReframeJob
from src.reframe.renderer import render_reframed_clip
from src.reframe.speaker_selection import select_primary_track_with_asd
from src.reframe.storage import reframe_output_path
from src.tracking.face_tracker import FaceTracker
from src.utils.db import DATA_DIR

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


def _detect_and_track(video_path):
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ReframeError(f"could not open clip: {video_path}", stage="detecting")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    sample_interval = max(1, round(fps / SAMPLE_FPS))

    all_tracks = []
    detector = FaceDetector()
    tracker = FaceTracker()
    try:
        frame_index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_index % sample_interval == 0:
                boxes = detector.detect(frame, frame_index)
                all_tracks.extend(tracker.update(frame_index, boxes))
            frame_index += 1
    finally:
        cap.release()
        detector.close()

    return all_tracks, width, height, total_frames, fps


def run_reframe(db: Session, highlight_clip_id: str, force: bool = False) -> ReframeJob:
    clip = db.get(HighlightClip, highlight_clip_id)
    if clip is None:
        raise ValueError(f"highlight clip not found: {highlight_clip_id}")

    job = get_or_create_reframe_job(db, highlight_clip_id)

    if job.status == ReframeStatus.READY and not force:
        return job

    try:
        video_path = DATA_DIR / clip.output_path

        job.status = ReframeStatus.DETECTING
        job.error_stage = None
        job.error_message = None
        db.commit()

        all_tracks, width, height, total_frames, fps = _detect_and_track(video_path)

        job.status = ReframeStatus.TRACKING
        db.commit()

        job.status = ReframeStatus.CROPPING
        db.commit()

        primary_track_id = select_primary_track_with_asd(video_path, all_tracks, fps)
        primary_boxes = [t for t in all_tracks if t.track_id == primary_track_id]
        crop_w = compute_crop_width(width, height)
        crop_path = build_crop_path(primary_boxes, width, height, total_frames)

        job.status = ReframeStatus.RENDERING
        db.commit()

        out_path = reframe_output_path(highlight_clip_id)
        render_reframed_clip(video_path, crop_path, crop_w, out_path)

        job.output_path = str(out_path.relative_to(DATA_DIR))
        job.status = ReframeStatus.READY
        db.commit()

    except ReframeError as e:
        job.status = ReframeStatus.FAILED
        job.error_stage = e.stage
        job.error_message = e.message
        db.commit()
    except Exception as e:
        job.status = ReframeStatus.FAILED
        job.error_stage = "unknown"
        job.error_message = str(e)
        db.commit()

    db.refresh(job)
    return job
