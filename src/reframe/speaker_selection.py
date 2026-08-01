import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from src.reframe.track_continuity import split_into_segments
from src.tracking.schemas import TrackedFace

module_logger = logging.getLogger(__name__)

ASD_MIN_TRACK_SAMPLES = 3


@dataclass
class TrackSegment:
    track_id: int
    segment_index: int
    boxes: list[TrackedFace]


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


def _heuristic_fallback(tracks: list[TrackedFace], log: logging.Logger) -> list[TrackedFace]:
    track_id = select_primary_track(tracks)
    if track_id is None:
        return []
    return [t for t in tracks if t.track_id == track_id]


def _build_segments(tracks: list[TrackedFace], fps: float, log: logging.Logger) -> list[TrackSegment]:
    """Groups tracks by track_id, then splits each track_id's samples into
    continuity segments (see track_continuity.split_into_segments) -- a single
    track_id can silently switch identity mid-clip (ByteTrack ID switch when
    two faces are close/overlapping), so a "track" here is no longer trusted
    as one person for its whole span.
    """
    tracks_by_id: dict[int, list[TrackedFace]] = defaultdict(list)
    for t in tracks:
        tracks_by_id[t.track_id].append(t)

    segments = []
    for track_id, boxes in tracks_by_id.items():
        try:
            sorted_boxes = sorted(boxes, key=lambda b: b.frame_index)
            chunks = split_into_segments(sorted_boxes, fps)
        except Exception:
            log.exception(
                "segment splitting failed for track %s, treating it as one unsplit segment",
                track_id,
            )
            chunks = [sorted(boxes, key=lambda b: b.frame_index)]
        for i, chunk in enumerate(chunks):
            segments.append(TrackSegment(track_id=track_id, segment_index=i, boxes=chunk))
    return segments


def select_primary_track_with_asd(
    video_path: Path,
    tracks: list[TrackedFace],
    source_fps: float,
    logger: logging.Logger | None = None,
) -> list[TrackedFace]:
    """Real Light-ASD-driven active-speaker selection, falling back to the pure
    heuristic above whenever ASD deps/weights aren't available or scoring fails for
    any reason — the pipeline behaves exactly as it did before this function existed
    until a real GPU instance actually has torch/python_speech_features installed.

    Returns the boxes of the single best-scoring track *segment* (see
    track_continuity.split_into_segments) rather than a whole track_id — a
    track_id can span an undetected identity switch, so scoring/selecting at
    the segment level avoids picking a "primary" that's secretly two people.
    """
    log = logger or module_logger
    if not tracks:
        return []

    try:
        from src.reframe.asd_scoring import LightASDScorer
    except ImportError:
        log.info("method=heuristic reason=Light-ASD dependencies not available (ImportError)")
        return _heuristic_fallback(tracks, log)

    segments = _build_segments(tracks, source_fps, log)

    candidates = [seg for seg in segments if len(seg.boxes) >= ASD_MIN_TRACK_SAMPLES]
    if not candidates:
        log.info("method=heuristic reason=no track segment has enough samples for ASD scoring")
        return _heuristic_fallback(tracks, log)

    try:
        scorer = LightASDScorer(logger=log)
    except Exception:
        log.exception("method=heuristic reason=failed to load Light-ASD model")
        return _heuristic_fallback(tracks, log)

    try:
        best_segment, best_score = None, float("-inf")
        all_scores: dict[str, float] = {}
        for seg in candidates:
            seg_key = f"{seg.track_id}.{seg.segment_index}"
            try:
                scores = scorer.score_track(video_path, seg.boxes, source_fps)
            except Exception:
                log.exception("Light-ASD scoring failed for segment %s, skipping", seg_key)
                continue
            if not scores:
                continue
            mean_score = sum(scores) / len(scores)
            all_scores[seg_key] = mean_score
            if mean_score > best_score:
                best_segment, best_score = seg, mean_score

        log.info("Light-ASD scores per track segment: %s", all_scores)
        if best_segment is None:
            log.info("method=heuristic reason=no track segment produced a usable ASD score")
            return _heuristic_fallback(tracks, log)
        log.info(
            "method=light-asd track_id=%s segment=%s score=%.3f",
            best_segment.track_id, best_segment.segment_index, best_score,
        )
        return best_segment.boxes
    finally:
        scorer.close()
