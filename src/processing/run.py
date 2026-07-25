import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

from src.processing.enums import ProcessingStatus
from src.processing.service import run_processing
from src.utils.db import SessionLocal, init_db


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and pre-process a ready ingestion job"
    )
    parser.add_argument("--job-id", required=True, help="Ingestion job id")
    parser.add_argument(
        "--force", action="store_true", help="Reprocess even if already ready"
    )
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        job = run_processing(db, args.job_id, force=args.force)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()

    if job.status == ProcessingStatus.FAILED:
        print(f"[{job.error_stage}] {job.error_message}", file=sys.stderr)
        sys.exit(1)

    print(f"processing job {job.id} ready")
    print(f"  video: {job.video_path}")
    print(f"  audio: {job.audio_path}")


if __name__ == "__main__":
    main()
