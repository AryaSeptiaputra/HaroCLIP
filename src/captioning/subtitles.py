import json
from dataclasses import dataclass
from pathlib import Path

from src.transcription.schemas import TranscriptSegment, TranscriptWord

DEFAULT_MAX_WORDS = 4
DEFAULT_MAX_DURATION = 1.5


@dataclass
class CaptionCue:
    start: float
    end: float
    text: str


def load_transcript(transcript_path: Path) -> list[TranscriptSegment]:
    raw = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
    segments = []
    for seg in raw:
        words = [TranscriptWord(**w) for w in seg.get("words", [])]
        segments.append(
            TranscriptSegment(start=seg["start"], end=seg["end"], text=seg["text"], words=words)
        )
    return segments


def slice_words_to_clip(
    segments: list[TranscriptSegment], start_seconds: float, end_seconds: float
) -> list[TranscriptWord]:
    """Flattens every word across all segments, keeps only those overlapping
    [start_seconds, end_seconds], and shifts timestamps to be clip-local
    (0 == start_seconds), for burning captions onto a clip that only covers that
    window of the original video.
    """
    sliced: list[TranscriptWord] = []
    for segment in segments:
        for word in segment.words:
            if word.end <= start_seconds or word.start >= end_seconds:
                continue
            sliced.append(
                TranscriptWord(
                    word=word.word,
                    start=word.start - start_seconds,
                    end=word.end - start_seconds,
                )
            )
    sliced.sort(key=lambda w: w.start)
    return sliced


def build_burst_cues(
    words: list[TranscriptWord],
    max_words: int = DEFAULT_MAX_WORDS,
    max_duration: float = DEFAULT_MAX_DURATION,
) -> list[CaptionCue]:
    """Greedily groups consecutive words into short "burst" cues (2-4 words,
    TikTok-style), closing a cue once adding the next word would exceed
    max_words or max_duration, whichever comes first.
    """
    cues: list[CaptionCue] = []
    current: list[TranscriptWord] = []

    def flush() -> None:
        if current:
            cues.append(
                CaptionCue(
                    start=current[0].start,
                    end=current[-1].end,
                    text=" ".join(w.word for w in current).strip(),
                )
            )

    for word in words:
        if current and (
            len(current) >= max_words or word.end - current[0].start > max_duration
        ):
            flush()
            current = []
        current.append(word)
    flush()

    return cues


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def write_srt(cues: list[CaptionCue], out_path: Path) -> None:
    blocks = [
        f"{index}\n"
        f"{_format_srt_timestamp(cue.start)} --> {_format_srt_timestamp(cue.end)}\n"
        f"{cue.text}\n"
        for index, cue in enumerate(cues, start=1)
    ]
    Path(out_path).write_text("\n".join(blocks), encoding="utf-8")
