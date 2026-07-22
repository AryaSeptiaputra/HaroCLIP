from src.ingestion.exceptions import IngestionValidationError
from src.ingestion.metadata import VideoMetadata

YTDLP_SOCKET_TIMEOUT_SECONDS = 15

YTDLP_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "noplaylist": True,
    "socket_timeout": YTDLP_SOCKET_TIMEOUT_SECONDS,
    "extract_flat": "in_playlist",
}


def validate_platform_url(url: str) -> VideoMetadata:
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL(YTDLP_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise IngestionValidationError(str(e), stage="validation")

    if info is None:
        raise IngestionValidationError(
            "yt-dlp could not resolve any info for this link", stage="validation"
        )

    if info.get("_type") == "playlist":
        raise IngestionValidationError(
            "playlist links are not supported, provide a single video URL",
            stage="validation",
        )

    formats = info.get("formats") or []
    has_video = any(f.get("vcodec") not in (None, "none") for f in formats)
    has_audio = any(f.get("acodec") not in (None, "none") for f in formats)

    if not (has_video and has_audio):
        raise IngestionValidationError(
            "link does not resolve to a video with both a video and an audio stream",
            stage="validation",
        )

    return VideoMetadata(
        duration_seconds=info.get("duration"),
        width=info.get("width"),
        height=info.get("height"),
        video_codec=info.get("vcodec"),
        audio_codec=info.get("acodec"),
        title=info.get("title"),
        raw=_strip_resolved_urls(info),
    )


def _strip_resolved_urls(info: dict) -> dict:
    # info/formats carry short-lived signed stream URLs (e.g. googlevideo.com).
    # source_url is the durable identifier stored on the job; these must not be
    # persisted, since they expire and re-resolving happens later at download time.
    sanitized = {k: v for k, v in info.items() if k not in ("url", "formats", "requested_formats", "requested_downloads")}
    return sanitized
