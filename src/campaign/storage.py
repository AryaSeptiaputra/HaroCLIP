from pathlib import Path

from src.utils.db import DATA_DIR

BRIEFS_DIR = DATA_DIR / "briefs"


def ensure_briefs_dir() -> Path:
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    return BRIEFS_DIR
