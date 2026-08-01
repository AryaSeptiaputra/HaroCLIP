import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.highlights.enums import HighlightStatus
from src.utils.db import Base


class HighlightJob(Base):
    __tablename__ = "highlight_jobs"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Plain string reference to IngestionJob.id — no real FK (SQLite, loose
    # cross-module coupling, same pattern used throughout this codebase).
    ingestion_job_id: Mapped[str] = mapped_column(
        String, nullable=False, unique=True, index=True
    )
    status: Mapped[HighlightStatus] = mapped_column(
        Enum(HighlightStatus, native_enum=False),
        nullable=False,
        default=HighlightStatus.PENDING,
    )
    transcript_path: Mapped[str | None] = mapped_column(String, nullable=True)
    # Raw Claude API response, cached to disk the moment a call succeeds — lets a
    # resume after a later-stage failure (parsing/rendering) reuse it instead of
    # re-calling the paid API. See src/highlights/service.py.
    llm_response_path: Mapped[str | None] = mapped_column(String, nullable=True)
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


class HighlightClip(Base):
    __tablename__ = "highlight_clips"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Plain string reference to HighlightJob.id — no real FK, same convention.
    highlight_job_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    # Authoritative source for rendering/captioning: JSON list of {"start", "end"}
    # dicts, one entry per source-video segment (>1 entry = a jump-cut clip). See
    # src/rendering/clipper.py and src/captioning/subtitles.py.
    segments_json: Mapped[str] = mapped_column(Text, nullable=False)
    # Overall span (segments[0].start, segments[-1].end) — display/logging only now;
    # rendering and captioning read segments_json instead. Kept so existing log
    # lines/CLI output that read these columns need no changes.
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    output_path: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
