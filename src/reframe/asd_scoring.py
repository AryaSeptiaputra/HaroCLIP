import logging
import math
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

from src.reframe.exceptions import ReframeError
from src.tracking.schemas import TrackedFace
from src.utils.logging import log_vram

WEIGHTS_PATH = (
    Path(__file__).resolve().parent.parent
    / "detection" / "light_asd" / "weight" / "pretrain_AVA_CVPR.model"
)

# Light-ASD's audio-visual alignment (4 MFCC frames : 1 video frame) is calibrated
# for 25fps video input — Columbia_test.py's own pipeline forces `-r 25` on
# everything it feeds the model. Our source clips aren't guaranteed to be 25fps
# (highlights-module's ffmpeg re-encode inherits whatever fps the source had), so we
# resample the interpolated crop sequence onto a synthetic 25fps timeline rather than
# assuming the source's native fps already matches.
MODEL_FPS = 25
AUDIO_SR = 16000
MFCC_NUMCEP = 13
MFCC_WINLEN = 0.025
MFCC_WINSTEP = 0.010
AUDIO_FPS = round(1.0 / MFCC_WINSTEP)  # 100

CROP_SIZE = 224
FACE_INPUT_SIZE = 112
CROP_SCALE = 0.40  # matches upstream's cropScale
PAD_VALUE = 110
MEDIAN_KERNEL = 13
DURATION_SET = (1, 1, 1, 2, 2, 2, 3, 3, 4, 5, 6)  # seconds; matches upstream's ensemble
MIN_TRACK_SAMPLES = 3
FFMPEG_TIMEOUT_SECONDS = 60


def _median_filter(values: list[float], kernel_size: int) -> list[float]:
    """Manual sliding-window median (odd kernel), clipped at the edges rather than
    zero-padded — scipy.signal.medfilt's default zero-padding would bias box
    center/size estimates toward zero near a track's boundaries, which is worse than
    just shrinking the window there. Avoids adding a scipy dependency for one call.
    """
    n = len(values)
    if n == 0 or kernel_size <= 1:
        return list(values)
    half = kernel_size // 2
    return [
        float(np.median(values[max(0, i - half): min(n, i + half + 1)]))
        for i in range(n)
    ]


class LightASDScorer:
    def __init__(self, weights_path: str | None = None, logger: logging.Logger | None = None):
        import torch

        from src.detection.light_asd.asd import ASD

        self._torch = torch
        path = weights_path or WEIGHTS_PATH
        if logger:
            logger.info("loading Light-ASD weights=%s", path)
        self._model = ASD()
        self._model.loadParameters(str(path))
        self._model.eval()
        if logger:
            log_vram(logger, "Light-ASD load")

    def close(self) -> None:
        del self._model
        if self._torch.cuda.is_available():
            self._torch.cuda.empty_cache()

    def score_track(
        self, video_path: Path, track_boxes: list[TrackedFace], source_fps: float
    ) -> list[float]:
        if len(track_boxes) < MIN_TRACK_SAMPLES:
            raise ReframeError("track has too few samples to score", stage="cropping")

        visual_feature = self._build_visual_feature(video_path, track_boxes, source_fps)
        audio_feature = self._build_audio_feature(video_path, track_boxes, source_fps)
        return self._run_ensemble(audio_feature, visual_feature)

    def _build_visual_feature(self, video_path, track_boxes, source_fps) -> np.ndarray:
        import cv2

        sorted_boxes = sorted(track_boxes, key=lambda b: b.frame_index)
        t_start = sorted_boxes[0].frame_index / source_fps
        t_end = sorted_boxes[-1].frame_index / source_fps
        sample_times = np.arange(t_start, max(t_end, t_start + 1.0 / MODEL_FPS), 1.0 / MODEL_FPS)

        sample_frame_times = [b.frame_index / source_fps for b in sorted_boxes]
        cx = [(b.x1 + b.x2) / 2 for b in sorted_boxes]
        cy = [(b.y1 + b.y2) / 2 for b in sorted_boxes]
        size = [max(b.x2 - b.x1, b.y2 - b.y1) / 2 for b in sorted_boxes]

        cx_i = _median_filter(np.interp(sample_times, sample_frame_times, cx).tolist(), MEDIAN_KERNEL)
        cy_i = _median_filter(np.interp(sample_times, sample_frame_times, cy).tolist(), MEDIAN_KERNEL)
        size_i = _median_filter(np.interp(sample_times, sample_frame_times, size).tolist(), MEDIAN_KERNEL)

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise ReframeError(f"could not open clip for ASD scoring: {video_path}", stage="cropping")

        crops = []
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, sorted_boxes[0].frame_index)
            last_index = sorted_boxes[0].frame_index - 1
            last_frame = None
            for i, t in enumerate(sample_times):
                target_index = round(t * source_fps)
                while last_index < target_index:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    last_frame = frame
                    last_index += 1
                if last_frame is None:
                    continue
                crops.append(self._crop_face(last_frame, cx_i[i], cy_i[i], size_i[i]))
        finally:
            cap.release()

        return np.array(crops, dtype=np.uint8)

    def _crop_face(self, frame: np.ndarray, cx: float, cy: float, size: float) -> np.ndarray:
        import cv2

        bs = max(size, 1.0)
        bsi = int(bs * (1 + 2 * CROP_SCALE))
        padded = np.pad(frame, ((bsi, bsi), (bsi, bsi), (0, 0)), mode="constant", constant_values=PAD_VALUE)
        my = cy + bsi
        mx = cx + bsi
        y0, y1 = int(my - bs), int(my + bs * (1 + 2 * CROP_SCALE))
        x0, x1 = int(mx - bs * (1 + CROP_SCALE)), int(mx + bs * (1 + CROP_SCALE))
        face = padded[y0:y1, x0:x1]
        face = cv2.resize(face, (CROP_SIZE, CROP_SIZE))
        face = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
        half = FACE_INPUT_SIZE // 2
        center = CROP_SIZE // 2
        return face[center - half: center + half, center - half: center + half]

    def _build_audio_feature(self, video_path, track_boxes, source_fps) -> np.ndarray:
        import python_speech_features

        sorted_boxes = sorted(track_boxes, key=lambda b: b.frame_index)
        t_start = sorted_boxes[0].frame_index / source_fps
        t_end = (sorted_boxes[-1].frame_index + 1) / source_fps
        duration = max(t_end - t_start, MFCC_WINLEN)

        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "track_audio.wav"
            _extract_audio_segment(video_path, t_start, duration, wav_path)
            audio = _read_wav_mono(wav_path)

        return python_speech_features.mfcc(
            audio, AUDIO_SR, numcep=MFCC_NUMCEP, winlen=MFCC_WINLEN, winstep=MFCC_WINSTEP
        )

    def _run_ensemble(self, audio_feature: np.ndarray, visual_feature: np.ndarray) -> list[float]:
        torch = self._torch

        audio_seconds = (audio_feature.shape[0] - audio_feature.shape[0] % 4) / AUDIO_FPS
        video_seconds = visual_feature.shape[0] / MODEL_FPS
        length = min(audio_seconds, video_seconds)
        if length <= 0:
            return []

        audio_frame_count = int(round(length * AUDIO_FPS))
        video_frame_count = int(round(length * MODEL_FPS))
        audio_feature = audio_feature[:audio_frame_count, :]
        visual_feature = visual_feature[:video_frame_count, :, :]

        all_scores = []
        for duration in DURATION_SET:
            batch_size = math.ceil(length / duration)
            scores = []
            with torch.no_grad():
                for i in range(batch_size):
                    a_chunk = audio_feature[i * duration * AUDIO_FPS:(i + 1) * duration * AUDIO_FPS, :]
                    v_chunk = visual_feature[i * duration * MODEL_FPS:(i + 1) * duration * MODEL_FPS, :, :]
                    if len(a_chunk) == 0 or len(v_chunk) == 0:
                        continue
                    input_a = torch.FloatTensor(a_chunk).unsqueeze(0).to(self._model.device)
                    input_v = torch.FloatTensor(v_chunk).unsqueeze(0).to(self._model.device)
                    embed_a = self._model.model.forward_audio_frontend(input_a)
                    embed_v = self._model.model.forward_visual_frontend(input_v)
                    out = self._model.model.forward_audio_visual_backend(embed_a, embed_v)
                    score = self._model.lossAV.forward(out, labels=None)
                    scores.extend(score)
            if scores:
                all_scores.append(scores)

        if not all_scores:
            return []
        min_len = min(len(s) for s in all_scores)
        trimmed = [s[:min_len] for s in all_scores]
        return list(np.mean(np.array(trimmed), axis=0))


def _extract_audio_segment(video_path: Path, start: float, duration: float, out_path: Path) -> None:
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-t", str(duration),
                "-i", str(video_path),
                "-vn", "-ac", "1", "-ar", str(AUDIO_SR), "-acodec", "pcm_s16le",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise ReframeError("ffmpeg audio segment extraction timed out", stage="cropping")

    if result.returncode != 0:
        raise ReframeError(
            f"ffmpeg audio segment extraction failed: {result.stderr.strip()}", stage="cropping"
        )


def _read_wav_mono(wav_path: Path) -> np.ndarray:
    with wave.open(str(wav_path), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16)
