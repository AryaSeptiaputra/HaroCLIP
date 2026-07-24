import numpy as np

from src.tracking.schemas import TrackedFace

CROP_ASPECT_W = 9
CROP_ASPECT_H = 16
DEFAULT_SMOOTHING_WINDOW = 15


def compute_crop_width(source_width: int, source_height: int) -> int:
    crop_w = round(source_height * CROP_ASPECT_W / CROP_ASPECT_H)
    return min(crop_w, source_width)


def build_crop_path(
    primary_track_boxes: list[TrackedFace],
    source_width: int,
    source_height: int,
    total_frames: int,
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
    interpolated = np.interp(frame_indices, sample_frames, sample_x)
    smoothed = _moving_average(interpolated, smoothing_window)
    clamped = np.clip(smoothed, 0, max_x).astype(int)

    return [(int(x), 0) for x in clamped]


def _moving_average(values: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(values) <= 1:
        return values
    window = min(window, len(values))
    kernel = np.ones(window) / window
    pad_left = window // 2
    pad_right = window - pad_left - 1
    padded = np.pad(values, (pad_left, pad_right), mode="edge")
    return np.convolve(padded, kernel, mode="valid")
