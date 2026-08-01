from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class PlatformVideoMetadata:
    description: Optional[str]
    upload_date: Optional[str]  # raw yt-dlp "YYYYMMDD" string, not parsed to a date
    uploader: Optional[str]
    platform: Optional[str]  # yt-dlp extractor_key, e.g. "Youtube", "TikTok"


def extract_platform_metadata(info: dict[str, Any]) -> PlatformVideoMetadata:
    return PlatformVideoMetadata(
        description=info.get("description"),
        upload_date=info.get("upload_date"),
        uploader=info.get("uploader"),
        platform=info.get("extractor_key"),
    )
