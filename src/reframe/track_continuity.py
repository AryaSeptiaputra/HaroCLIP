from src.tracking.schemas import TrackedFace

# Untuned heuristic default -- no real two-person footage exists locally to
# calibrate against (see CLAUDE.md verification caveat). Pixels/second,
# normalized by real elapsed time (frame_index delta / fps) rather than a raw
# per-sample pixel delta, so one constant is meaningful regardless of the
# clip's native fps or how many frames a gap in detection spans.
MAX_JUMP_PX_PER_SEC = 1800.0
MIN_DT_SECONDS = 1e-3  # guards div-by-zero for duplicate/same-frame samples


def find_discontinuities(
    samples: list[TrackedFace],
    fps: float,
    max_jump_px_per_sec: float = MAX_JUMP_PX_PER_SEC,
) -> list[int]:
    """samples must already be sorted by frame_index. Returns indices i such
    that samples[i-1] -> samples[i] is an implausible x-jump, signaling a
    likely tracker identity switch rather than real motion.
    """
    if fps <= 0 or len(samples) < 2:
        return []

    breaks = []
    for i in range(1, len(samples)):
        prev, cur = samples[i - 1], samples[i]
        dt = max((cur.frame_index - prev.frame_index) / fps, MIN_DT_SECONDS)
        prev_cx = (prev.x1 + prev.x2) / 2
        cur_cx = (cur.x1 + cur.x2) / 2
        velocity = abs(cur_cx - prev_cx) / dt
        if velocity > max_jump_px_per_sec:
            breaks.append(i)
    return breaks


def split_into_segments(
    samples: list[TrackedFace],
    fps: float,
    max_jump_px_per_sec: float = MAX_JUMP_PX_PER_SEC,
) -> list[list[TrackedFace]]:
    """Splits samples (sorted by frame_index) into contiguous segments at each
    detected discontinuity. No discontinuity -> a single segment containing
    all samples, unchanged.
    """
    breaks = find_discontinuities(samples, fps, max_jump_px_per_sec)
    if not breaks:
        return [samples]

    segments = []
    start = 0
    for b in breaks:
        segments.append(samples[start:b])
        start = b
    segments.append(samples[start:])
    return segments
