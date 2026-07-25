from dataclasses import dataclass


@dataclass
class TrackedFace:
    frame_index: int
    track_id: int
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
