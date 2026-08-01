import re
from pathlib import Path

from src.utils.db import DATA_DIR

CAPTIONS_DIR = DATA_DIR / "captions"
CAPTIONED_DIR = DATA_DIR / "captioned"

# Long YouTube titles could otherwise produce filesystem-unfriendly filenames.
SLUG_MAX_LENGTH = 80


def slugify(text: str | None) -> str:
    if not text:
        return ""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return slug[:SLUG_MAX_LENGTH].strip("_")


def caption_ass_path(highlight_clip_id: str) -> Path:
    CAPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    return CAPTIONS_DIR / f"{highlight_clip_id}.ass"


def captioned_output_path(video_title: str | None, rank: int) -> Path:
    CAPTIONED_DIR.mkdir(parents=True, exist_ok=True)
    slug = slugify(video_title) or "video"
    return CAPTIONED_DIR / f"{slug}_captioned_{rank:02d}.mp4"
