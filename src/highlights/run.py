import argparse
import sys

from src.highlights.enums import HighlightStatus
from src.highlights.models import HighlightClip
from src.highlights.service import run_highlight_detection
from src.utils.db import SessionLocal, init_db


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transcribe a processed video, detect highlight clips, and render them"
    )
    parser.add_argument("--job-id", required=True, help="Ingestion job id")
    parser.add_argument(
        "--force", action="store_true", help="Redo even if already ready"
    )
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        job = run_highlight_detection(db, args.job_id, force=args.force)
        if job.status == HighlightStatus.FAILED:
            print(f"[{job.error_stage}] {job.error_message}", file=sys.stderr)
            sys.exit(1)

        clips = (
            db.query(HighlightClip)
            .filter_by(highlight_job_id=job.id)
            .order_by(HighlightClip.rank)
            .all()
        )
        print(f"highlight job {job.id} ready")
        for clip in clips:
            print(f"  #{clip.rank} [{clip.start_seconds:.1f}-{clip.end_seconds:.1f}] {clip.output_path}")
            print(f"      reason: {clip.reason}")
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
