import enum


class HighlightStatus(str, enum.Enum):
    PENDING = "pending"
    TRANSCRIBING = "transcribing"
    DETECTING = "detecting"
    RENDERING = "rendering"
    READY = "ready"
    FAILED = "failed"
