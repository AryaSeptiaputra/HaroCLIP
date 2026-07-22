import type { TrackedJob } from "../hooks/useTrackedJobs";
import { StatusBadge } from "./StatusBadge";
import styles from "./JobListItem.module.css";

interface JobListItemProps {
  trackedJob: TrackedJob;
  selected: boolean;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
}

function truncate(value: string, max = 60): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

export function JobListItem({ trackedJob, selected, onSelect, onRemove }: JobListItemProps) {
  const { id, job, status } = trackedJob;
  const label = job?.title || job?.source_url || id;

  return (
    <li className={`${styles.item} ${selected ? styles.selected : ""}`}>
      <button className={styles.select} type="button" onClick={() => onSelect(id)}>
        <span className={styles.label}>{truncate(label)}</span>
        <StatusBadge status={status} />
      </button>
      <button
        className={styles.remove}
        type="button"
        title="Remove from list"
        onClick={(e) => {
          e.stopPropagation();
          onRemove(id);
        }}
      >
        ×
      </button>
    </li>
  );
}
