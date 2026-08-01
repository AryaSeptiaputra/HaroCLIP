from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from src.campaign_brief.exceptions import CampaignBriefError
from src.campaign_brief.service import apply_campaign_brief
from src.ingestion.models import IngestionJob
from src.ingestion.schemas import IngestionJobRead
from src.utils.db import get_db
from src.utils.logging import get_job_logger

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.post("/jobs/{job_id}/campaign-brief", response_model=IngestionJobRead)
async def upload_campaign_brief(
    job_id: str,
    file: UploadFile = File(...),
    # Request-scoped only — never logged, never persisted (see
    # src/campaign_brief/summarizer.py). Falls back to the server's own
    # ANTHROPIC_API_KEY env var when omitted.
    anthropic_api_key: str | None = Form(None),
    db: Session = Depends(get_db),
):
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="file must be a PDF")

    pdf_bytes = await file.read()
    logger = get_job_logger("campaign_brief", job_id)

    try:
        job = apply_campaign_brief(
            db, job_id, pdf_bytes, file.filename, logger=logger, api_key=anthropic_api_key
        )
    except CampaignBriefError as e:
        raise HTTPException(status_code=422, detail=f"[{e.stage}] {e.message}")

    return job
