from pathlib import Path

from src.utils.db import DATA_DIR

BRIEFS_DIR = DATA_DIR / "campaign_briefs"


def campaign_brief_dir(ingestion_job_id: str) -> Path:
    d = BRIEFS_DIR / ingestion_job_id
    d.mkdir(parents=True, exist_ok=True)
    return d
