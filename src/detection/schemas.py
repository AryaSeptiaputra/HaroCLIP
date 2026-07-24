from dataclasses import dataclass


@dataclass
class FaceBox:
    frame_index: int
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
