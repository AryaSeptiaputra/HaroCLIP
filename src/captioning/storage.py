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


def caption_ass_path(ingestion_job_id: str, highlight_clip_id: str) -> Path:
    job_dir = CAPTIONS_DIR / ingestion_job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir / f"{highlight_clip_id}.ass"


def captioned_output_path(ingestion_job_id: str, video_title: str | None, rank: int) -> Path:
    job_dir = CAPTIONED_DIR / ingestion_job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(video_title) or "video"
    return job_dir / f"{slug}_captioned_{rank:02d}.mp4"
