from functools import lru_cache
from urllib.parse import urlparse

from src.ingestion.enums import LinkType

DIRECT_MEDIA_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".m3u8", ".ts"}


@lru_cache(maxsize=1)
def _platform_extractors():
    from yt_dlp.extractor import gen_extractor_classes
    from yt_dlp.extractor.generic import GenericIE

    return [cls for cls in gen_extractor_classes() if cls is not GenericIE]


def detect_link_type(url: str) -> LinkType:
    path = urlparse(url).path.lower()
    if any(path.endswith(ext) for ext in DIRECT_MEDIA_EXTENSIONS):
        return LinkType.DIRECT

    for extractor in _platform_extractors():
        try:
            if extractor.suitable(url):
                return LinkType.PLATFORM
        except Exception:
            continue

    return LinkType.DIRECT
