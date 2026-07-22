import type { TrackedJob } from "../hooks/useTrackedJobs";
import { StatusBadge } from "./StatusBadge";
import styles from "./JobDetail.module.css";

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function elapsedSeconds(isoTimestamp: string): number {
  return Math.round((Date.now() - new Date(isoTimestamp).getTime()) / 1000);
}

export function JobDetail({ trackedJob }: { trackedJob: TrackedJob | null }) {
  if (!trackedJob) {
    return <p className={styles.empty}>Select a job to see its details.</p>;
  }

  const { job, status } = trackedJob;

  if (status === "not_found") {
    return (
      <div className={styles.panel}>
        <StatusBadge status={status} />
        <p className={styles.message}>
          This job is no longer available on the server. You can remove it from the list.
        </p>
      </div>
    );
  }

  if (!job) {
    return (
      <div className={styles.panel}>
        <StatusBadge status={status} />
        <p className={styles.message}>Loading job details…</p>
      </div>
    );
  }

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <StatusBadge status={status} />
        <span className={styles.linkType}>{job.link_type ?? "unknown"}</span>
      </div>

      <p className={styles.url}>{job.source_url}</p>

      {job.status === "ready" && (
        <dl className={styles.meta}>
          {job.title && (
            <>
              <dt>Title</dt>
              <dd>{job.title}</dd>
            </>
          )}
          <dt>Duration</dt>
          <dd>{formatDuration(job.duration_seconds)}</dd>
          <dt>Resolution</dt>
          <dd>{job.width && job.height ? `${job.width} x ${job.height}` : "—"}</dd>
          <dt>Video codec</dt>
          <dd>{job.video_codec ?? "—"}</dd>
          <dt>Audio codec</dt>
          <dd>{job.audio_codec ?? "—"}</dd>
        </dl>
      )}

      {job.status === "failed" && (
        <div className={styles.errorBox}>
          <p className={styles.errorStage}>Stage: {job.error_stage ?? "unknown"}</p>
          <p className={styles.errorMessage}>{job.error_message}</p>
        </div>
      )}

      {(job.status === "pending" || job.status === "validating") && (
        <p className={styles.message}>
          Still validating… ({elapsedSeconds(job.created_at)}s elapsed. This can take up to ~20s for
          direct links or ~15s for platform links.)
        </p>
      )}
    </div>
  );
}
