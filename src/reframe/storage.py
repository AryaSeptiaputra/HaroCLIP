from pathlib import Path

from src.utils.db import DATA_DIR

REFRAMED_DIR = DATA_DIR / "reframed"


def reframe_output_path(highlight_clip_id: str) -> Path:
    REFRAMED_DIR.mkdir(parents=True, exist_ok=True)
    return REFRAMED_DIR / f"{highlight_clip_id}.mp4"
