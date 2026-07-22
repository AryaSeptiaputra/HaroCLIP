from pathlib import Path

from src.utils.db import DATA_DIR

VIDEOS_DIR = DATA_DIR / "videos"


def job_dir(ingestion_job_id: str) -> Path:
    d = VIDEOS_DIR / ingestion_job_id
    d.mkdir(parents=True, exist_ok=True)
    return d
