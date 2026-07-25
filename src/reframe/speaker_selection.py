import logging
from collections import defaultdict
from pathlib import Path

from src.tracking.schemas import TrackedFace

logger = logging.getLogger(__name__)

ASD_MIN_TRACK_SAMPLES = 3


def select_primary_track(tracks: list[TrackedFace]) -> int | None:
    """Heuristic placeholder for real active-speaker detection.

    Picks the face track with the largest total on-screen area (summed across
    every frame it appears in) as a proxy for "main subject", tie-broken by
    frame count (longest-persisting track).
    """
    if not tracks:
        return None

    area_by_track: dict[int, float] = defaultdict(float)
    frames_by_track: dict[int, int] = defaultdict(int)
    for t in tracks:
        area_by_track[t.track_id] += (t.x2 - t.x1) * (t.y2 - t.y1)
        frames_by_track[t.track_id] += 1

    return max(
        area_by_track,
        key=lambda track_id: (area_by_track[track_id], frames_by_track[track_id]),
    )


def select_primary_track_with_asd(
    video_path: Path, tracks: list[TrackedFace], source_fps: float
) -> int | None:
    """Real Light-ASD-driven active-speaker selection, falling back to the pure
    heuristic above whenever ASD deps/weights aren't available or scoring fails for
    any reason — the pipeline behaves exactly as it did before this function existed
    until a real GPU instance actually has torch/python_speech_features installed.
    """
    if not tracks:
        return None

    try:
        from src.reframe.asd_scoring import LightASDScorer
    except ImportError:
        logger.info("Light-ASD dependencies not available, using heuristic speaker selection")
        return select_primary_track(tracks)

    tracks_by_id: dict[int, list[TrackedFace]] = defaultdict(list)
    for t in tracks:
        tracks_by_id[t.track_id].append(t)

    candidates = {
        track_id: boxes
        for track_id, boxes in tracks_by_id.items()
        if len(boxes) >= ASD_MIN_TRACK_SAMPLES
    }
    if not candidates:
        return select_primary_track(tracks)

    try:
        scorer = LightASDScorer()
    except Exception:
        logger.exception("failed to load Light-ASD model, using heuristic speaker selection")
        return select_primary_track(tracks)

    try:
        best_track_id, best_score = None, float("-inf")
        for track_id, boxes in candidates.items():
            try:
                scores = scorer.score_track(video_path, boxes, source_fps)
            except Exception:
                logger.exception("Light-ASD scoring failed for track %s, skipping", track_id)
                continue
            if not scores:
                continue
            mean_score = sum(scores) / len(scores)
            if mean_score > best_score:
                best_track_id, best_score = track_id, mean_score

        if best_track_id is None:
            return select_primary_track(tracks)
        return best_track_id
    finally:
        scorer.close()
