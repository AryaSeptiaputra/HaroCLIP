import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.captioning.enums import CaptionStatus
from src.utils.db import Base


class CaptionJob(Base):
    __tablename__ = "caption_jobs"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Plain string reference to HighlightClip.id — no real FK, same loose-coupling
    # convention used throughout this codebase (see ReframeJob.highlight_clip_id).
    highlight_clip_id: Mapped[str] = mapped_column(
        String, nullable=False, unique=True, index=True
    )
    status: Mapped[CaptionStatus] = mapped_column(
        Enum(CaptionStatus, native_enum=False),
        nullable=False,
        default=CaptionStatus.PENDING,
    )
    srt_path: Mapped[str | None] = mapped_column(String, nullable=True)
    output_path: Mapped[str | None] = mapped_column(String, nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
