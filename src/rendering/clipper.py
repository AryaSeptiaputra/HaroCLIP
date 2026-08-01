import subprocess
from pathlib import Path

from src.rendering.exceptions import RenderingError

# 600 (not 120): confirmed on a real run that a CPU-bound libx264 re-encode of a
# high-resolution source clip can exceed 120s even for a well-under-MAX_CLIP_SECONDS
# duration. Generous margin for up to a full 60s (MAX_CLIP_SECONDS) clip even at a
# pessimistic below-realtime encode rate. See src/processing/downloader.py for the
# companion fix (capping download resolution) that reduces how often this matters.
FFMPEG_TIMEOUT_SECONDS = 600


def render_clip(
    source_video: Path, segments: list[tuple[float, float]], out_path: Path
) -> None:
    # One -ss/-i pair per segment (input-side -ss, same reasoning as before: -t is a
    # duration relative to the seek point, avoiding the input-side-ss/output-side-to
    # gotcha) so ffmpeg seeks straight to each segment instead of decoding the whole
    # source video from the start — matters even for a single segment deep into a
    # long source video, and matters a lot for a jump-cut segment near the end of one.
    # Each seeked input's PTS resets via setpts/asetpts, then the concat filter
    # stitches them into one continuous output with a hard cut at each boundary.
    # This also correctly handles the N=1 (non-jump-cut) case with no special-casing.
    input_args: list[str] = []
    for start, end in segments:
        input_args += ["-ss", str(start), "-t", str(end - start), "-i", str(source_video)]

    filter_parts = []
    concat_inputs = []
    for i in range(len(segments)):
        filter_parts.append(f"[{i}:v]setpts=PTS-STARTPTS[v{i}]")
        filter_parts.append(f"[{i}:a]asetpts=PTS-STARTPTS[a{i}]")
        concat_inputs.append(f"[v{i}][a{i}]")
    filter_complex = (
        ";".join(filter_parts)
        + ";"
        + "".join(concat_inputs)
        + f"concat=n={len(segments)}:v=1:a=1[outv][outa]"
    )

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                *input_args,
                "-filter_complex", filter_complex,
                "-map", "[outv]",
                "-map", "[outa]",
                "-c:v", "libx264",
                "-c:a", "aac",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise RenderingError(
            f"ffmpeg timed out after {FFMPEG_TIMEOUT_SECONDS}s", stage="rendering"
        )

    if result.returncode != 0:
        raise RenderingError(
            f"ffmpeg clip render failed: {result.stderr.strip()}", stage="rendering"
        )
