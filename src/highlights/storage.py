from pathlib import Path

from src.utils.db import DATA_DIR

CLIPS_DIR = DATA_DIR / "clips"


def clip_job_dir(ingestion_job_id: str) -> Path:
    d = CLIPS_DIR / ingestion_job_id
    d.mkdir(parents=True, exist_ok=True)
    return d
