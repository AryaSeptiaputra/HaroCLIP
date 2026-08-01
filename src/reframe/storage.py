from pathlib import Path

from src.utils.db import DATA_DIR

REFRAMED_DIR = DATA_DIR / "reframed"


def reframe_output_path(ingestion_job_id: str, highlight_clip_id: str) -> Path:
    job_dir = REFRAMED_DIR / ingestion_job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir / f"{highlight_clip_id}.mp4"
