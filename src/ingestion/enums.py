import enum


class LinkType(str, enum.Enum):
    DIRECT = "direct"
    PLATFORM = "platform"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"
