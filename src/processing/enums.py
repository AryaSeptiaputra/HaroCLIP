import enum


class ProcessingStatus(str, enum.Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    EXTRACTING_AUDIO = "extracting_audio"
    READY = "ready"
    FAILED = "failed"
