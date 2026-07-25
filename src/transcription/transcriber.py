import logging
import os
import time
from pathlib import Path

from src.transcription.schemas import TranscriptSegment
from src.utils.logging import log_vram

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = "int8"


def transcribe(audio_path: Path, logger: logging.Logger | None = None) -> list[TranscriptSegment]:
    import torch
    from faster_whisper import WhisperModel

    if logger:
        logger.info("loading whisper model=%s device=%s compute_type=%s", WHISPER_MODEL_SIZE, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE)
    load_start = time.monotonic()
    model = WhisperModel(
        WHISPER_MODEL_SIZE, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE
    )
    if logger:
        logger.info("whisper model loaded in %.1fs", time.monotonic() - load_start)
        log_vram(logger, "whisper model load")
    try:
        transcribe_start = time.monotonic()
        segments, _info = model.transcribe(str(audio_path))
        result = [
            TranscriptSegment(start=seg.start, end=seg.end, text=seg.text.strip())
            for seg in segments
        ]
        if logger:
            logger.info(
                "transcription done in %.1fs: %d segments", time.monotonic() - transcribe_start, len(result)
            )
        return result
    finally:
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
