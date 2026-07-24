from dataclasses import dataclass


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
