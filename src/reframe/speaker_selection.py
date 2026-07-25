import logging
from collections import defaultdict
from pathlib import Path

from src.tracking.schemas import TrackedFace

module_logger = logging.getLogger(__name__)

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
    video_path: Path,
    tracks: list[TrackedFace],
    source_fps: float,
    logger: logging.Logger | None = None,
) -> int | None:
    """Real Light-ASD-driven active-speaker selection, falling back to the pure
    heuristic above whenever ASD deps/weights aren't available or scoring fails for
    any reason — the pipeline behaves exactly as it did before this function existed
    until a real GPU instance actually has torch/python_speech_features installed.
    """
    log = logger or module_logger
    if not tracks:
        return None

    try:
        from src.reframe.asd_scoring import LightASDScorer
    except ImportError:
        log.info("method=heuristic reason=Light-ASD dependencies not available (ImportError)")
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
        log.info("method=heuristic reason=no track has enough samples for ASD scoring")
        return select_primary_track(tracks)

    try:
        scorer = LightASDScorer(logger=log)
    except Exception:
        log.exception("method=heuristic reason=failed to load Light-ASD model")
        return select_primary_track(tracks)

    try:
        best_track_id, best_score = None, float("-inf")
        all_scores: dict[int, float] = {}
        for track_id, boxes in candidates.items():
            try:
                scores = scorer.score_track(video_path, boxes, source_fps)
            except Exception:
                log.exception("Light-ASD scoring failed for track %s, skipping", track_id)
                continue
            if not scores:
                continue
            mean_score = sum(scores) / len(scores)
            all_scores[track_id] = mean_score
            if mean_score > best_score:
                best_track_id, best_score = track_id, mean_score

        log.info("Light-ASD scores per track: %s", all_scores)
        if best_track_id is None:
            log.info("method=heuristic reason=no track produced a usable ASD score")
            return select_primary_track(tracks)
        log.info("method=light-asd track_id=%s score=%.3f", best_track_id, best_score)
        return best_track_id
    finally:
        scorer.close()
