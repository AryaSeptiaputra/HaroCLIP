import logging

from sqlalchemy.orm import Session

from src.campaign_brief.storage import campaign_brief_dir
from src.campaign_brief.summarizer import summarize_campaign_brief
from src.ingestion.models import IngestionJob
from src.utils.db import DATA_DIR


def apply_campaign_brief(
    db: Session,
    ingestion_job_id: str,
    pdf_bytes: bytes,
    filename: str,
    logger: logging.Logger | None = None,
    api_key: str | None = None,
) -> IngestionJob:
    job = db.get(IngestionJob, ingestion_job_id)
    if job is None:
        raise ValueError(f"ingestion job not found: {ingestion_job_id}")

    dest_dir = campaign_brief_dir(ingestion_job_id)

    # Persist the PDF before calling Claude, unconditionally — it's the input, known
    # up front, and worth keeping for provenance/debugging even if the API call below
    # fails. This is the specific gap the old (removed) src/campaign/ module never
    # closed: it stored uploaded files but never actually used their content.
    pdf_path = dest_dir / "brief.pdf"
    pdf_path.write_bytes(pdf_bytes)

    # api_key is a request-scoped credential (never DB/file-persisted, see
    # summarize_campaign_brief) — passed straight through, not stored alongside the
    # PDF/response artifacts above.
    summary = summarize_campaign_brief(pdf_bytes, logger=logger, api_key=api_key)

    response_path = dest_dir / "claude_response.txt"
    response_path.write_text(summary, encoding="utf-8")

    job.campaign_context = summary
    db.commit()
    db.refresh(job)

    if logger:
        logger.info(
            "campaign brief applied: pdf=%s summary=%d chars",
            pdf_path.relative_to(DATA_DIR), len(summary),
        )
    return job
