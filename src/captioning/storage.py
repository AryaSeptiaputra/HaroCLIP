from pathlib import Path

from src.utils.db import DATA_DIR

CAPTIONS_DIR = DATA_DIR / "captions"
CAPTIONED_DIR = DATA_DIR / "captioned"


def caption_ass_path(highlight_clip_id: str) -> Path:
    CAPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    return CAPTIONS_DIR / f"{highlight_clip_id}.ass"


def captioned_output_path(highlight_clip_id: str) -> Path:
    CAPTIONED_DIR.mkdir(parents=True, exist_ok=True)
    return CAPTIONED_DIR / f"{highlight_clip_id}.mp4"
