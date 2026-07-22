from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.campaign.enums import BriefContentType


class CampaignBriefRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    content_type: BriefContentType
    raw_text: str | None
    original_filename: str | None
    mime_type: str | None
    file_size_bytes: int | None
    created_at: datetime
    updated_at: datetime
