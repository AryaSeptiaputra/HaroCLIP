import subprocess
from pathlib import Path

from src.rendering.exceptions import RenderingError

# 600 (not 120): confirmed on a real run that a CPU-bound libx264 re-encode of a
# high-resolution source clip can exceed 120s even for a well-under-MAX_CLIP_SECONDS
# duration. Generous margin for up to a full 180s (MAX_CLIP_SECONDS) clip even at a
# pessimistic below-realtime encode rate. See src/processing/downloader.py for the
# companion fix (capping download resolution) that reduces how often this matters.
FFMPEG_TIMEOUT_SECONDS = 600


def render_clip(source_video: Path, start: float, end: float, out_path: Path) -> None:
    # -ss and -t are both given as input options (before -i) so -t is a duration
    # relative to the seek point, not an absolute position in the original
    # timeline — combining input-side -ss with output-side -to is a well-known
    # ffmpeg gotcha where -to still means "in the original timeline", silently
    # producing a 0-length or misaligned clip.
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-t", str(end - start),
                "-i", str(source_video),
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
