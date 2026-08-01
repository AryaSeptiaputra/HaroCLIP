import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.processing.enums import ProcessingStatus
from src.utils.db import Base


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Plain string reference to IngestionJob.id — no real FK (SQLite, loose
    # cross-module coupling, same pattern used throughout this codebase).
    ingestion_job_id: Mapped[str] = mapped_column(
        String, nullable=False, unique=True, index=True
    )
    status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, native_enum=False),
        nullable=False,
        default=ProcessingStatus.PENDING,
    )
    video_path: Mapped[str | None] = mapped_column(String, nullable=True)
    audio_path: Mapped[str | None] = mapped_column(String, nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # yt-dlp metadata captured at download time (PLATFORM links only; always None for
    # DIRECT links, which never have a yt-dlp info dict). Distinct from
    # IngestionJob.raw_metadata, which is captured earlier at validation time via a
    # shallower extract_flat call — this is the full-depth info dict already fetched
    # by download_platform_video, at no extra network cost.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    upload_date: Mapped[str | None] = mapped_column(String, nullable=True)
    uploader: Mapped[str | None] = mapped_column(String, nullable=True)
    platform: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
