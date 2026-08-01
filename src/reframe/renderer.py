import subprocess
import threading
from pathlib import Path

from src.reframe.exceptions import ReframeError

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
# 600 (not 120): this is now a real CPU-bound libx264 encode of the piped crop
# frames, not the old pure stream-copy remux — same reasoning as
# src/rendering/clipper.py and src/captioning/service.py's 600s budgets for a
# full re-encode of a full-length clip.
FFMPEG_TIMEOUT_SECONDS = 600
# 17 (not left at libx264's default 23, and not matched to captioning's final
# -crf 18): this crop render is an INTERMEDIATE artifact that captioning
# re-encodes again on top of — two stacked lossy re-encodes compound visible
# loss more than one clearly-above-final-quality intermediate plus one final
# pass does. File size doesn't matter here since captioning immediately
# re-encodes it; this replaces the old cv2.VideoWriter(fourcc="mp4v") path,
# which was a real quality bottleneck (mp4v-encode -> stream-copy -> later
# libx264 re-encode, stacking a weak intermediate codec ahead of the
# quality-defining final pass). Not yet visually verified against real footage
# (same caveat as the rest of this module).
CROP_CRF = "17"


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

    # Single ffmpeg process: cropped/resized frames piped in raw over stdin as
    # one input, the original clip as a second input for audio (stream-copied),
    # video encoded directly to libx264 — no OpenCV-encoded intermediate file
    # and no second remux subprocess.
    proc = subprocess.Popen(
        [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{OUTPUT_WIDTH}x{OUTPUT_HEIGHT}", "-r", str(fps),
            "-i", "-",
            "-i", str(source_clip),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-crf", CROP_CRF, "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-shortest",
            str(out_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # Drain stderr on a background thread WHILE writing frames to stdin —
    # ffmpeg's own progress/log output can fill the OS pipe buffer during a
    # longer encode; if nothing reads stderr while this process is blocked
    # writing stdin, both sides can deadlock.
    stderr_chunks: list[bytes] = []

    def _drain_stderr() -> None:
        stderr_chunks.append(proc.stderr.read())

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

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
            proc.stdin.write(resized.tobytes())
            frame_index += 1
    except BrokenPipeError:
        pass  # ffmpeg exited early — the real error surfaces via returncode below
    finally:
        cap.release()
        proc.stdin.close()

    try:
        proc.wait(timeout=FFMPEG_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise ReframeError(
            f"ffmpeg crop render timed out after {FFMPEG_TIMEOUT_SECONDS}s", stage="rendering"
        )

    stderr_thread.join()

    if proc.returncode != 0:
        stderr = b"".join(stderr_chunks).decode(errors="replace")
        raise ReframeError(f"ffmpeg crop render failed: {stderr.strip()}", stage="rendering")
