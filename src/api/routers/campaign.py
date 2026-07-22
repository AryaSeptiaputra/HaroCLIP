from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from src.campaign import service
from src.campaign.exceptions import CampaignBriefValidationError
from src.campaign.schemas import CampaignBriefRead
from src.utils.db import get_db

router = APIRouter(prefix="/campaign", tags=["campaign"])


@router.post("/briefs", response_model=CampaignBriefRead, status_code=201)
def create_brief(
    title: str = Form(...),
    raw_text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    try:
        return service.create_brief(db, title, raw_text, file)
    except CampaignBriefValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.get("/briefs", response_model=list[CampaignBriefRead])
def list_briefs(db: Session = Depends(get_db)):
    return service.list_briefs(db)


@router.get("/briefs/{brief_id}", response_model=CampaignBriefRead)
def get_brief(brief_id: str, db: Session = Depends(get_db)):
    brief = service.get_brief(db, brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="brief not found")
    return brief


@router.get("/briefs/{brief_id}/file")
def download_brief_file(brief_id: str, db: Session = Depends(get_db)):
    brief = service.get_brief(db, brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="brief not found")
    try:
        path = service.get_brief_file_path(brief)
    except CampaignBriefValidationError as e:
        raise HTTPException(status_code=404, detail=e.message)
    return FileResponse(
        path, media_type=brief.mime_type, filename=brief.original_filename
    )
