from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl

from src.ingestion.enums import JobStatus, LinkType


class IngestionJobCreate(BaseModel):
    source_url: HttpUrl


class IngestionJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_url: str
    link_type: LinkType | None
    status: JobStatus
    duration_seconds: float | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None
    title: str | None
    error_stage: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
