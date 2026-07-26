import logging
import os
import time
from pathlib import Path

from src.transcription.schemas import TranscriptSegment, TranscriptWord
from src.utils.logging import log_vram

# large-v3 is already the largest/most accurate standard faster-whisper checkpoint —
# there's no bigger official variant to move up to (distil-whisper trades accuracy for
# speed, the opposite of what we want here). Model-quality gains beyond this come from
# inference settings, not a bigger model — see vad_filter below.
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
# float16 (not int8, and deliberately not float32 either): with the highlight-detection
# LLM moved to the Claude API, this is now the only model resident during highlight
# detection — VRAM budget would allow float32, but that roughly doubles VRAM/time for
# no meaningful WER improvement over float16 on this model. Considered and rejected
# during the 2026-07-26 local-model quality pass, see docs/hardware-spec.md.
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")


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
        # word_timestamps=True: needed for word-burst caption generation
        # (src/captioning/) — same model, no extra VRAM/pass, faster-whisper derives
        # per-word timing from the same forward pass as the segment-level text.
        # vad_filter=True: strips silence before decoding — faster-whisper's own
        # recommended setting for real-world (non-clean) audio, reduces hallucinated
        # text during silence/music. beam_size is left at the library default (5),
        # already the recommended value; higher has diminishing/negative returns.
        segments, _info = model.transcribe(
            str(audio_path),
            word_timestamps=True,
            vad_filter=True,
            vad_parameters=dict(
                threshold=0.5,
                min_speech_duration_ms=250,
                min_silence_duration_ms=500,
            ),
        )
        result = [
            TranscriptSegment(
                start=seg.start,
                end=seg.end,
                text=seg.text.strip(),
                words=[
                    TranscriptWord(word=w.word.strip(), start=w.start, end=w.end)
                    for w in (seg.words or [])
                ],
            )
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
