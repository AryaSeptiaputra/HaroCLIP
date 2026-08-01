import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.reframe.asd_scoring import MIN_TRACK_SAMPLES as ASD_MIN_TRACK_SAMPLES
from src.reframe.asd_scoring import MODEL_FPS
from src.reframe.track_continuity import split_into_segments
from src.tracking.schemas import TrackedFace

module_logger = logging.getLogger(__name__)

# Both untuned heuristics -- no real multi-speaker footage exists locally to
# calibrate against (same caveat category as track_continuity.py's
# MAX_JUMP_PX_PER_SEC). MIN_DWELL_SECONDS anchors loosely off
# captioning/subtitles.py's own 1.5s cue-closing threshold as an
# order-of-magnitude guess for "a natural minimum unit of continuous speech"
# in this codebase, not a principled derivation. SCORE_MARGIN is ~8% of the
# raw Light-ASD logit range actually observed in a real vast.ai run's log
# (roughly -2.9 to +0.9) -- wide enough that noise between two similar
# candidates shouldn't flip a switch, narrow enough that a real turn change
# (which the same log showed as an enormous gap, e.g. 0.9 vs -1.0) clears it
# easily. Needs real-footage tuning on the next vast.ai run.
MIN_DWELL_SECONDS = 1.5
SCORE_MARGIN = 0.3


@dataclass
class TrackSegment:
    track_id: int
    segment_index: int
    boxes: list[TrackedFace]


@dataclass
class SpeakerWindow:
    """A contiguous span of a reframe clip during which one track/segment is
    treated as the active speaker to follow. A list of these always covers
    [0, total_frames) with no gaps -- a whole-clip single-speaker selection
    (today's original behavior) is just the degenerate one-window case.

    `boxes` holds the *entire* winning segment's samples, not just the ones
    inside [start_frame, end_frame) -- crop_path.py masks its writes to the
    window's own frame range, but needs the full sample set so edge-hold/
    interpolation at the window's own boundaries behaves like the existing
    per-segment interpolation elsewhere in this module.
    """

    start_frame: int
    end_frame: int  # exclusive
    track_id: int | None
    segment_index: int | None
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


def _segment_heuristic_fallback(
    segments: list["TrackSegment"], log: logging.Logger
) -> TrackSegment | None:
    """Continuity-aware version of _heuristic_fallback: picks the single best
    TrackSegment by cumulative bbox area (tie-broken by sample count), never a
    raw/unsplit track_id. Guaranteed non-None whenever `segments` is non-empty,
    which _build_segments guarantees whenever the source `tracks` was non-empty
    (every track_id produces >=1 segment even with zero discontinuities) -- this
    is what keeps build_crop_path() from being handed an empty list (and thus
    freeze-centering) just because no individual segment cleared the ASD
    eligibility floor, which becomes common once a scene has 3+ people.

    Returns the whole TrackSegment (not just its boxes) so callers can carry
    its track_id/segment_index through into a SpeakerWindow for logging.
    """
    if not segments:
        return None

    def _area(seg: "TrackSegment") -> float:
        return sum((b.x2 - b.x1) * (b.y2 - b.y1) for b in seg.boxes)

    best = max(segments, key=lambda s: (_area(s), len(s.boxes)))
    log.info(
        "method=segment-heuristic track_id=%s segment=%s samples=%d",
        best.track_id, best.segment_index, len(best.boxes),
    )
    return best


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


def _score_frame_indices(seg: TrackSegment, num_scores: int, source_fps: float) -> list[int]:
    """Maps LightASDScorer.score_track()'s per-frame score sequence back to
    the clip's real frame_index space. score_track (asd_scoring.py) samples
    at t_start + i/MODEL_FPS for i = 0, 1, 2, ... where t_start is this
    segment's own first sample's timestamp, and only ever truncates from the
    end -- so scores[i] always corresponds to sample i of that same sequence,
    with no re-derivation needed here.
    """
    t_start = seg.boxes[0].frame_index / source_fps
    return [round((t_start + i / MODEL_FPS) * source_fps) for i in range(num_scores)]


def _max_velocity_px_per_sec(boxes: list[TrackedFace], fps: float) -> float:
    """Diagnostic only (see _build_speaker_windows) -- same center-x
    velocity formula as track_continuity.find_discontinuities, but purely
    logged, not used to trigger a split here.
    """
    if fps <= 0 or len(boxes) < 2:
        return 0.0
    sorted_boxes = sorted(boxes, key=lambda b: b.frame_index)
    best = 0.0
    for prev, cur in zip(sorted_boxes, sorted_boxes[1:]):
        dt = max((cur.frame_index - prev.frame_index) / fps, 1e-3)
        prev_cx = (prev.x1 + prev.x2) / 2
        cur_cx = (cur.x1 + cur.x2) / 2
        best = max(best, abs(cur_cx - prev_cx) / dt)
    return best


def _center_x_std(boxes: list[TrackedFace]) -> float:
    if not boxes:
        return 0.0
    return float(np.std([(b.x1 + b.x2) / 2 for b in boxes]))


def _score_candidates(
    scorer, video_path: Path, candidates: list[TrackSegment], source_fps: float, log: logging.Logger,
) -> dict[str, list[float]]:
    """Scores every candidate segment once, returning each one's full
    per-frame score sequence (not collapsed to a mean) -- shared by both
    _best_segment_by_mean_score (the old whole-clip-winner fallback) and
    _build_speaker_windows (the new per-moment timeline), so this is the
    single place Light-ASD is actually invoked. Candidates that fail to
    score are simply absent from the returned dict.
    """
    scores_by_seg: dict[str, list[float]] = {}
    mean_by_seg: dict[str, float] = {}
    for seg in candidates:
        seg_key = f"{seg.track_id}.{seg.segment_index}"
        try:
            scores = scorer.score_track(video_path, seg.boxes, source_fps)
        except Exception:
            log.exception("Light-ASD scoring failed for segment %s, skipping", seg_key)
            continue
        if not scores:
            continue
        scores_by_seg[seg_key] = scores
        mean_by_seg[seg_key] = sum(scores) / len(scores)
        log.info(
            "candidate segment %s: samples=%d span=[%d,%d] mean_score=%.3f center_x_std=%.1fpx",
            seg_key, len(seg.boxes), seg.boxes[0].frame_index, seg.boxes[-1].frame_index,
            mean_by_seg[seg_key], _center_x_std(seg.boxes),
        )

    log.info(
        "ASD segment scoring: %d/%d candidate segments produced a usable score",
        len(scores_by_seg), len(candidates),
    )
    log.info("Light-ASD scores per track segment: %s", mean_by_seg)
    return scores_by_seg


def _best_segment_by_mean_score(
    candidates: list[TrackSegment], scores_by_seg: dict[str, list[float]], log: logging.Logger,
) -> TrackSegment | None:
    """Replicates this module's original (pre-timeline) selection logic
    exactly: pick the single segment with the highest mean score across its
    whole span. Used both as the timeline construction's own error fallback
    and to keep this simpler whole-clip behavior available at all -- since
    scoring already happened once in _score_candidates, falling back here
    costs zero additional Light-ASD inference.
    """
    best_seg, best_score = None, float("-inf")
    for seg in candidates:
        seg_key = f"{seg.track_id}.{seg.segment_index}"
        scores = scores_by_seg.get(seg_key)
        if not scores:
            continue
        mean_score = sum(scores) / len(scores)
        if mean_score > best_score:
            best_seg, best_score = seg, mean_score

    if best_seg is None:
        return None
    log.info(
        "method=light-asd track_id=%s segment=%s score=%.3f",
        best_seg.track_id, best_seg.segment_index, best_score,
    )
    return best_seg


def _build_speaker_windows(
    candidates: list[TrackSegment],
    scores_by_seg: dict[str, list[float]],
    fps: float,
    total_frames: int,
    log: logging.Logger,
) -> list[SpeakerWindow]:
    """Winner-take-all-with-hysteresis sweep over every frame: at each frame,
    the "active" candidates are those whose own score curve covers that
    frame (no extrapolation beyond a candidate's own observed span); the raw
    winner is whichever active candidate scores highest right now. A switch
    away from the current incumbent only commits once a single challenger
    has led by >= SCORE_MARGIN for >= MIN_DWELL_SECONDS of consecutive
    frames, which absorbs momentary ASD noise/short interjections. A frame
    with no active candidate at all holds the incumbent (edge-hold, same
    convention np.interp/build_crop_path already use elsewhere).

    Raises if no candidate ever has any active coverage -- the caller treats
    that as an error and falls back to _best_segment_by_mean_score, same as
    any other exception here.
    """
    candidates_by_key = {f"{s.track_id}.{s.segment_index}": s for s in candidates}
    curves: dict[str, tuple[list[int], list[float]]] = {}
    for seg_key, scores in scores_by_seg.items():
        seg = candidates_by_key[seg_key]
        frame_idxs = _score_frame_indices(seg, len(scores), fps)
        curves[seg_key] = (frame_idxs, scores)

    dwell_frames = max(1, round(MIN_DWELL_SECONDS * fps))

    windows: list[tuple[int, int, str]] = []
    current_key: str | None = None
    current_start = 0
    current_last_score = float("-inf")
    challenge_key: str | None = None
    challenge_start = 0
    challenge_streak = 0

    for f in range(total_frames):
        active: dict[str, float] = {}
        for seg_key, (frame_idxs, scores) in curves.items():
            if frame_idxs[0] <= f <= frame_idxs[-1]:
                active[seg_key] = float(np.interp(f, frame_idxs, scores))
        raw_winner = max(active, key=active.get) if active else None

        if current_key is None:
            if raw_winner is not None:
                current_key = raw_winner
                current_start = 0
                current_last_score = active[raw_winner]
            continue

        # Track the incumbent's own live score on every frame it has coverage,
        # independent of whether it's the momentary raw winner -- otherwise a
        # challenger's margin gets compared against a stale, higher score from
        # whenever the incumbent last happened to be winning, instead of its
        # actual current (possibly already-declining) score.
        if current_key in active:
            current_last_score = active[current_key]

        if raw_winner is None or raw_winner == current_key:
            challenge_key, challenge_streak = None, 0
            continue

        if active[raw_winner] >= current_last_score + SCORE_MARGIN:
            if challenge_key == raw_winner:
                challenge_streak += 1
            else:
                challenge_key, challenge_start, challenge_streak = raw_winner, f, 1

            if challenge_streak >= dwell_frames:
                windows.append((current_start, challenge_start, current_key))
                current_key = challenge_key
                current_start = challenge_start
                current_last_score = active[raw_winner]
                challenge_key, challenge_streak = None, 0
        else:
            challenge_key, challenge_streak = None, 0

    if current_key is None:
        raise RuntimeError("no candidate segment ever had active score coverage")
    windows.append((current_start, total_frames, current_key))

    result = []
    for start, end, seg_key in windows:
        seg = candidates_by_key[seg_key]
        window_boxes = [b for b in seg.boxes if start <= b.frame_index < end]
        log.info(
            "speaker window: frames=[%d,%d) (%.1f-%.1fs) track_id=%s segment=%s "
            "center_x_std=%.1fpx max_velocity_px_s=%.0f",
            start, end, start / fps, end / fps, seg.track_id, seg.segment_index,
            _center_x_std(window_boxes), _max_velocity_px_per_sec(window_boxes, fps),
        )
        result.append(
            SpeakerWindow(
                start_frame=start, end_frame=end,
                track_id=seg.track_id, segment_index=seg.segment_index, boxes=seg.boxes,
            )
        )
    return result


def select_speaker_timeline(
    video_path: Path,
    tracks: list[TrackedFace],
    source_fps: float,
    total_frames: int,
    logger: logging.Logger | None = None,
) -> list[SpeakerWindow]:
    """Dynamic multi-speaker-follow selection: instead of picking one
    "primary speaker" for a clip's entire duration (this module's original
    behavior, still used as the fallback whenever the timeline machinery
    below can't run), builds a timeline of SpeakerWindows so the crop can
    switch to whoever's actually speaking at each point in the clip.

    Always returns a non-empty list covering [0, total_frames) -- the
    degenerate single-window case (ASD unavailable, no eligible segment,
    model load failure, or the timeline construction itself failing) is
    exactly today's original whole-clip selection, wrapped as one window, so
    every existing fallback guarantee (never a hard failure just because ASD
    deps/weights/scoring aren't available) is preserved unchanged.
    """
    log = logger or module_logger
    if not tracks:
        log.info("method=none reason=no face tracks detected in clip")
        return [SpeakerWindow(0, total_frames, None, None, [])]

    def _whole_clip_window(seg: TrackSegment | None) -> list[SpeakerWindow]:
        if seg is None:
            return [SpeakerWindow(0, total_frames, None, None, [])]
        return [SpeakerWindow(0, total_frames, seg.track_id, seg.segment_index, seg.boxes)]

    segments = _build_segments(tracks, source_fps, log)

    try:
        from src.reframe.asd_scoring import LightASDScorer
    except ImportError:
        log.info("reason=Light-ASD dependencies not available (ImportError)")
        return _whole_clip_window(_segment_heuristic_fallback(segments, log))

    candidates = [seg for seg in segments if len(seg.boxes) >= ASD_MIN_TRACK_SAMPLES]
    log.info(
        "segment summary: %d raw tracks -> %d continuity segments (%d/%d eligible for ASD, floor=%d)",
        len({s.track_id for s in segments}), len(segments),
        len(candidates), len(segments), ASD_MIN_TRACK_SAMPLES,
    )
    if not candidates:
        log.info("reason=no track segment has enough samples for ASD scoring")
        return _whole_clip_window(_segment_heuristic_fallback(segments, log))

    try:
        scorer = LightASDScorer(logger=log)
    except Exception:
        log.exception("reason=failed to load Light-ASD model")
        return _whole_clip_window(_segment_heuristic_fallback(segments, log))

    try:
        scores_by_seg = _score_candidates(scorer, video_path, candidates, source_fps, log)
        if not scores_by_seg:
            log.info("reason=no track segment produced a usable ASD score")
            return _whole_clip_window(_segment_heuristic_fallback(segments, log))

        try:
            return _build_speaker_windows(candidates, scores_by_seg, source_fps, total_frames, log)
        except Exception:
            log.exception(
                "dynamic speaker-timeline construction failed, falling back to single-best-segment selection"
            )
            best_seg = _best_segment_by_mean_score(candidates, scores_by_seg, log)
            if best_seg is None:
                return _whole_clip_window(_segment_heuristic_fallback(segments, log))
            return _whole_clip_window(best_seg)
    finally:
        scorer.close()
