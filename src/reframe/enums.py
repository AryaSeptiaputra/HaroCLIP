import enum


class ReframeStatus(str, enum.Enum):
    PENDING = "pending"
    DETECTING = "detecting"
    TRACKING = "tracking"
    CROPPING = "cropping"
    RENDERING = "rendering"
    READY = "ready"
    FAILED = "failed"
