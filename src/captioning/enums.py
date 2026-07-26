import enum


class CaptionStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    RENDERING = "rendering"
    READY = "ready"
    FAILED = "failed"
