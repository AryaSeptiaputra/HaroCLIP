import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

from src.captioning.enums import CaptionStatus
from src.captioning.service import run_captioning
from src.highlights.models import HighlightClip, HighlightJob
from src.utils.db import SessionLocal, init_db


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Burn word-burst captions onto every reframed highlight clip for an ingestion job"
    )
    parser.add_argument("--job-id", required=True, help="Ingestion job id")
    parser.add_argument(
        "--force", action="store_true", help="Redo even if already ready"
    )
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        highlight_job = (
            db.query(HighlightJob).filter_by(ingestion_job_id=args.job_id).one_or_none()
        )
        if highlight_job is None:
            print(f"no highlight job found for ingestion job {args.job_id}", file=sys.stderr)
            sys.exit(1)

        clips = (
            db.query(HighlightClip)
            .filter_by(highlight_job_id=highlight_job.id)
            .order_by(HighlightClip.rank)
            .all()
        )
        if not clips:
            print(f"highlight job {highlight_job.id} has no clips", file=sys.stderr)
            sys.exit(1)

        any_failed = False
        for clip in clips:
            job = run_captioning(db, clip.id, force=args.force)
            if job.status == CaptionStatus.FAILED:
                any_failed = True
                print(f"clip {clip.id} (#{clip.rank}): [{job.error_stage}] {job.error_message}", file=sys.stderr)
            else:
                print(f"clip {clip.id} (#{clip.rank}): ready -> {job.output_path}")

        if any_failed:
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
