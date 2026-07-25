import os
from pathlib import Path

from src.transcription.schemas import TranscriptSegment

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = "int8"


def transcribe(audio_path: Path) -> list[TranscriptSegment]:
    import torch
    from faster_whisper import WhisperModel

    model = WhisperModel(
        WHISPER_MODEL_SIZE, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE
    )
    try:
        segments, _info = model.transcribe(str(audio_path))
        return [
            TranscriptSegment(start=seg.start, end=seg.end, text=seg.text.strip())
            for seg in segments
        ]
    finally:
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
