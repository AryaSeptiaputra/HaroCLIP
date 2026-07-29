from pathlib import Path
from urllib.parse import urlparse

import httpx

from src.ingestion.link_detection import DIRECT_MEDIA_EXTENSIONS
from src.processing.exceptions import ProcessingError

DIRECT_DOWNLOAD_TIMEOUT_SECONDS = 300


def download_platform_video(url: str, dest_dir: Path) -> Path:
    import yt_dlp

    outtmpl = str(dest_dir / "source.%(ext)s")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        # Capped at 1080p (not "bestvideo", uncapped): the final deliverable is
        # always cropped down to a 1080-wide 9:16 vertical clip
        # (src/reframe/renderer.py), so downloading source resolution above 1080p
        # wastes disk, bandwidth, and CPU-bound re-encode time for zero quality
        # benefit. Confirmed on a real run: an uncapped download contributed to
        # both a disk-full failure and an ffmpeg render timeout downstream.
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
    except yt_dlp.utils.DownloadError as e:
        raise ProcessingError(str(e), stage="download")

    path = Path(filename)
    if not path.exists():
        # merge_output_format may change the final extension after postprocessing
        merged = dest_dir / f"source.{opts['merge_output_format']}"
        if merged.exists():
            return merged
        raise ProcessingError(
            f"expected downloaded file not found: {filename}", stage="download"
        )
    return path


def download_direct_video(url: str, dest_dir: Path) -> Path:
    ext = Path(urlparse(url).path).suffix.lower()
    if ext not in DIRECT_MEDIA_EXTENSIONS:
        ext = ".mp4"
    dest = dest_dir / f"source{ext}"

    try:
        with httpx.stream(
            "GET", url, timeout=DIRECT_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True
        ) as response:
            response.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)
    except httpx.HTTPError as e:
        raise ProcessingError(f"direct download failed: {e}", stage="download")

    return dest
