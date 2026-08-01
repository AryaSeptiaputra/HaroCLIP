import logging

import numpy as np

from src.reframe.track_continuity import split_into_segments
from src.tracking.schemas import TrackedFace

CROP_ASPECT_W = 9
CROP_ASPECT_H = 16
DEFAULT_SMOOTHING_WINDOW = 15

module_logger = logging.getLogger(__name__)


def compute_crop_width(source_width: int, source_height: int) -> int:
    crop_w = round(source_height * CROP_ASPECT_W / CROP_ASPECT_H)
    return min(crop_w, source_width)


def build_crop_path(
    primary_track_boxes: list[TrackedFace],
    source_width: int,
    source_height: int,
    total_frames: int,
    fps: float,
    smoothing_window: int = DEFAULT_SMOOTHING_WINDOW,
) -> list[tuple[int, int]]:
    crop_w = compute_crop_width(source_width, source_height)
    max_x = max(source_width - crop_w, 0)

    if not primary_track_boxes:
        center_x = max_x // 2
        return [(center_x, 0)] * total_frames

    samples = sorted(primary_track_boxes, key=lambda b: b.frame_index)
    sample_frames = [b.frame_index for b in samples]
    # left edge of the crop window such that it's centered on the face's midpoint
    sample_x = [((b.x1 + b.x2) / 2) - crop_w / 2 for b in samples]

    frame_indices = np.arange(total_frames)
    # np.interp holds fp[0]/fp[-1] for frame indices outside the sampled range —
    # exactly the "freeze-pan" behavior wanted before the first / after the last sample.
    flat_interpolated = np.interp(frame_indices, sample_frames, sample_x)

    try:
        interpolated = _interpolate_with_segment_breaks(
            samples, frame_indices, flat_interpolated, crop_w, fps
        )
    except Exception:
        module_logger.exception(
            "segment-aware interpolation failed, falling back to plain interpolation"
        )
        interpolated = flat_interpolated

    smoothed = _moving_average(interpolated, smoothing_window)
    clamped = np.clip(smoothed, 0, max_x).astype(int)

    return [(int(x), 0) for x in clamped]


def _interpolate_with_segment_breaks(
    samples: list[TrackedFace],
    frame_indices: np.ndarray,
    flat_interpolated: np.ndarray,
    crop_w: float,
    fps: float,
) -> np.ndarray:
    """Re-interpolates each track_continuity-detected segment independently,
    instead of letting a single flat np.interp draw a straight ramp across a
    likely tracker identity switch. Segments never interpolate across the gap
    between them -- the boundary is a hard cut at the frame midpoint between
    the two segments' nearest real samples, each side holding its own
    segment's interpolation (np.interp's edge-hold behavior handles this for
    free once each segment is interpolated on its own).
    """
    segments = split_into_segments(samples, fps)
    if len(segments) <= 1:
        return flat_interpolated

    result = flat_interpolated.copy()
    boundaries = [0]
    for prev_seg, next_seg in zip(segments, segments[1:]):
        prev_last = prev_seg[-1].frame_index
        next_first = next_seg[0].frame_index
        boundaries.append((prev_last + next_first) / 2)
    boundaries.append(frame_indices[-1] + 1)

    for seg, lo, hi in zip(segments, boundaries, boundaries[1:]):
        seg_frames = [b.frame_index for b in seg]
        seg_x = [((b.x1 + b.x2) / 2) - crop_w / 2 for b in seg]
        mask = (frame_indices >= lo) & (frame_indices < hi)
        if not mask.any():
            continue
        result[mask] = np.interp(frame_indices[mask], seg_frames, seg_x)

    return result


def _moving_average(values: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(values) <= 1:
        return values
    window = min(window, len(values))
    kernel = np.ones(window) / window
    pad_left = window // 2
    pad_right = window - pad_left - 1
    padded = np.pad(values, (pad_left, pad_right), mode="edge")
    return np.convolve(padded, kernel, mode="valid")
