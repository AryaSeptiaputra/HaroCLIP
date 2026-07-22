from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from src.ingestion import service
from src.ingestion.models import IngestionJob
from src.ingestion.schemas import IngestionJobCreate, IngestionJobRead
from src.utils.db import get_db

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.post("/jobs", response_model=IngestionJobRead, status_code=202)
def create_ingestion_job(
    payload: IngestionJobCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    job = service.create_job(db, str(payload.source_url))
    background_tasks.add_task(service.run_validation, job.id)
    return job


@router.get("/jobs/{job_id}", response_model=IngestionJobRead)
def get_ingestion_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job
