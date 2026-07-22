import subprocess
from pathlib import Path

from src.processing.exceptions import ProcessingError

FFMPEG_TIMEOUT_SECONDS = 600


def extract_audio(video_path: Path, audio_path: Path) -> None:
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise ProcessingError(
            f"ffmpeg timed out after {FFMPEG_TIMEOUT_SECONDS}s", stage="audio_extraction"
        )

    if result.returncode != 0:
        raise ProcessingError(
            f"ffmpeg audio extraction failed: {result.stderr.strip()}",
            stage="audio_extraction",
        )
