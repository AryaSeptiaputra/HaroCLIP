import argparse
import sys

from src.highlights.enums import HighlightStatus
from src.highlights.models import HighlightClip
from src.highlights.service import run_highlight_detection
from src.ingestion.enums import JobStatus
from src.ingestion.models import IngestionJob
from src.ingestion.service import create_job, run_validation
from src.processing.enums import ProcessingStatus
from src.processing.service import run_processing
from src.reframe.enums import ReframeStatus
from src.reframe.service import run_reframe
from src.utils.db import SessionLocal, init_db


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-shot end-to-end run: ingestion -> processing -> highlights -> reframe"
    )
    parser.add_argument("--url", help="Source video URL (submits a new ingestion job)")
    parser.add_argument(
        "--job-id",
        help="Resume from an existing ready ingestion job id instead of --url "
        "(skips ingestion; processing/highlights/reframe are already idempotent, "
        "so this safely resumes a partially-completed run without re-downloading)",
    )
    parser.add_argument(
        "--force", action="store_true", help="Force re-run of every stage even if already ready"
    )
    args = parser.parse_args()

    if bool(args.url) == bool(args.job_id):
        print("exactly one of --url or --job-id is required", file=sys.stderr)
        sys.exit(1)

    init_db()
    db = SessionLocal()
    try:
        if args.url:
            print(f"[1/4] Ingestion: submitting {args.url}")
            job = create_job(db, args.url)
            run_validation(job.id)
            db.refresh(job)
            if job.status != JobStatus.READY:
                print(f"FAILED at ingestion: [{job.error_stage}] {job.error_message}", file=sys.stderr)
                print(f"ingestion job id: {job.id}  (re-run with --job-id {job.id} once fixed)")
                sys.exit(1)
            print(
                f"[1/4] Ingestion OK: job_id={job.id} duration={job.duration_seconds}s "
                f"{job.width}x{job.height}"
            )
        else:
            job = db.get(IngestionJob, args.job_id)
            if job is None:
                print(f"no ingestion job found for id {args.job_id}", file=sys.stderr)
                sys.exit(1)
            if job.status != JobStatus.READY:
                print(f"ingestion job {job.id} is not ready (status={job.status.value})", file=sys.stderr)
                sys.exit(1)
            print(f"[1/4] Ingestion: resuming from existing job_id={job.id}")

        print("[2/4] Processing: downloading + extracting audio")
        proc = run_processing(db, job.id, force=args.force)
        if proc.status != ProcessingStatus.READY:
            print(f"FAILED at processing: [{proc.error_stage}] {proc.error_message}", file=sys.stderr)
            print(f"ingestion job id: {job.id}  (re-run with --job-id {job.id} once fixed)")
            sys.exit(1)
        print(f"[2/4] Processing OK: video={proc.video_path} audio={proc.audio_path}")

        print("[3/4] Highlights: transcribing + detecting + rendering")
        hjob = run_highlight_detection(db, job.id, force=args.force)
        if hjob.status != HighlightStatus.READY:
            print(f"FAILED at highlights: [{hjob.error_stage}] {hjob.error_message}", file=sys.stderr)
            print(f"ingestion job id: {job.id}  (re-run with --job-id {job.id} once fixed)")
            sys.exit(1)
        clips = (
            db.query(HighlightClip)
            .filter_by(highlight_job_id=hjob.id)
            .order_by(HighlightClip.rank)
            .all()
        )
        print(f"[3/4] Highlights OK: {len(clips)} clips")

        print("[4/4] Reframe: dynamic vertical-crop per clip")
        any_failed = False
        for clip in clips:
            rjob = run_reframe(db, clip.id, force=args.force)
            if rjob.status != ReframeStatus.READY:
                any_failed = True
                print(f"  clip #{clip.rank}: FAILED [{rjob.error_stage}] {rjob.error_message}", file=sys.stderr)
            else:
                print(f"  clip #{clip.rank}: OK -> {rjob.output_path}")

        print()
        print(f"=== DONE. ingestion job id: {job.id} ===")
        print(f"logs:        data/logs/{job.id}/")
        print(f"final clips: data/reframed/")
        if any_failed:
            print("one or more reframe stages failed, see above", file=sys.stderr)
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
