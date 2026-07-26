from dataclasses import dataclass, field


@dataclass
class TranscriptWord:
    word: str
    start: float
    end: float


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: list[TranscriptWord] = field(default_factory=list)
