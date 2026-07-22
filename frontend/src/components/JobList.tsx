import type { TrackedJob } from "../hooks/useTrackedJobs";
import { JobListItem } from "./JobListItem";
import styles from "./JobList.module.css";

interface JobListProps {
  trackedJobs: TrackedJob[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
}

export function JobList({ trackedJobs, selectedId, onSelect, onRemove }: JobListProps) {
  if (trackedJobs.length === 0) {
    return <p className={styles.empty}>No jobs submitted yet.</p>;
  }

  return (
    <ul className={styles.list}>
      {trackedJobs.map((trackedJob) => (
        <JobListItem
          key={trackedJob.id}
          trackedJob={trackedJob}
          selected={trackedJob.id === selectedId}
          onSelect={onSelect}
          onRemove={onRemove}
        />
      ))}
    </ul>
  );
}
