import subprocess
from pathlib import Path

from src.reframe.exceptions import ReframeError

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
FFMPEG_TIMEOUT_SECONDS = 120


def render_reframed_clip(
    source_clip: Path,
    crop_path: list[tuple[int, int]],
    crop_w: int,
    out_path: Path,
) -> None:
    import cv2

    cap = cv2.VideoCapture(str(source_clip))
    if not cap.isOpened():
        raise ReframeError(f"could not open source clip: {source_clip}", stage="rendering")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    source_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    video_only_path = out_path.with_name(out_path.stem + "_video_only.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(
        str(video_only_path), fourcc, fps, (OUTPUT_WIDTH, OUTPUT_HEIGHT)
    )

    try:
        frame_index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            path_index = min(frame_index, len(crop_path) - 1) if crop_path else 0
            x, y = crop_path[path_index] if crop_path else (0, 0)
            cropped = frame[y : y + source_height, x : x + crop_w]
            resized = cv2.resize(cropped, (OUTPUT_WIDTH, OUTPUT_HEIGHT))
            writer.write(resized)
            frame_index += 1
    finally:
        cap.release()
        writer.release()

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(video_only_path),
                "-i", str(source_clip),
                "-map", "0:v",
                "-map", "1:a",
                "-c:v", "copy",
                "-c:a", "copy",
                "-shortest",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise ReframeError(
            f"ffmpeg audio remux timed out after {FFMPEG_TIMEOUT_SECONDS}s",
            stage="rendering",
        )
    finally:
        video_only_path.unlink(missing_ok=True)

    if result.returncode != 0:
        raise ReframeError(
            f"ffmpeg audio remux failed: {result.stderr.strip()}", stage="rendering"
        )
