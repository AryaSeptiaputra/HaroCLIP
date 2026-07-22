from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class VideoMetadata:
    duration_seconds: Optional[float]
    width: Optional[int]
    height: Optional[int]
    video_codec: Optional[str]
    audio_codec: Optional[str]
    title: Optional[str]
    raw: dict[str, Any]
