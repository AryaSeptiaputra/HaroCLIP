import json
import subprocess

from src.ingestion.exceptions import IngestionValidationError
from src.ingestion.metadata import VideoMetadata

FFPROBE_TIMEOUT_SECONDS = 20


def validate_direct_url(url: str) -> VideoMetadata:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise IngestionValidationError(
            f"ffprobe timed out after {FFPROBE_TIMEOUT_SECONDS}s", stage="validation"
        )

    if result.returncode != 0:
        raise IngestionValidationError(
            f"ffprobe failed: {result.stderr.strip()}", stage="validation"
        )

    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise IngestionValidationError(
            "ffprobe returned malformed JSON", stage="validation"
        )

    streams = info.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video_stream is None or audio_stream is None:
        raise IngestionValidationError(
            "link does not contain both a video and an audio stream",
            stage="validation",
        )

    fmt = info.get("format", {})
    duration = fmt.get("duration")

    return VideoMetadata(
        duration_seconds=float(duration) if duration is not None else None,
        width=video_stream.get("width"),
        height=video_stream.get("height"),
        video_codec=video_stream.get("codec_name"),
        audio_codec=audio_stream.get("codec_name"),
        title=None,
        raw=info,
    )
