from pathlib import Path
from typing import Optional

from fastapi import UploadFile
from sqlalchemy.orm import Session

from src.campaign.enums import BriefContentType
from src.campaign.exceptions import CampaignBriefValidationError
from src.campaign.models import CampaignBrief
from src.campaign.storage import BRIEFS_DIR, ensure_briefs_dir
from src.utils.db import DATA_DIR

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}


def create_brief(
    db: Session,
    title: str,
    raw_text: Optional[str],
    file: Optional[UploadFile],
) -> CampaignBrief:
    if not title or not title.strip():
        raise CampaignBriefValidationError("title is required", field="title")

    has_text = bool(raw_text and raw_text.strip())
    has_file = file is not None and bool(file.filename)

    if has_text and has_file:
        raise CampaignBriefValidationError(
            "provide either pasted text or a file, not both", field="content"
        )
    if not has_text and not has_file:
        raise CampaignBriefValidationError(
            "either pasted text or a file is required", field="content"
        )

    if has_text:
        brief = CampaignBrief(
            title=title.strip(),
            content_type=BriefContentType.TEXT,
            raw_text=raw_text.strip(),
        )
        db.add(brief)
        db.commit()
        db.refresh(brief)
        return brief

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise CampaignBriefValidationError(
            f"unsupported file type: {ext or '(none)'}", field="file"
        )

    ensure_briefs_dir()
    brief = CampaignBrief(
        title=title.strip(),
        content_type=BriefContentType.FILE,
        original_filename=file.filename,
        mime_type=file.content_type,
    )
    db.add(brief)
    db.commit()
    db.refresh(brief)

    stored_path = BRIEFS_DIR / f"{brief.id}{ext}"
    contents = file.file.read()
    stored_path.write_bytes(contents)

    brief.file_path = str(stored_path.relative_to(DATA_DIR))
    brief.file_size_bytes = len(contents)
    db.commit()
    db.refresh(brief)
    return brief


def list_briefs(db: Session) -> list[CampaignBrief]:
    return db.query(CampaignBrief).order_by(CampaignBrief.created_at.desc()).all()


def get_brief(db: Session, brief_id: str) -> Optional[CampaignBrief]:
    return db.get(CampaignBrief, brief_id)


def get_brief_file_path(brief: CampaignBrief) -> Path:
    if brief.content_type != BriefContentType.FILE or not brief.file_path:
        raise CampaignBriefValidationError(
            "this brief has no uploaded file", field="file"
        )
    path = DATA_DIR / brief.file_path
    if not path.exists():
        raise CampaignBriefValidationError(
            "stored file is missing on disk", field="file"
        )
    return path
