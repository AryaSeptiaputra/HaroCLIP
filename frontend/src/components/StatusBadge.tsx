import type { TrackedJobStatus } from "../hooks/useTrackedJobs";
import styles from "./StatusBadge.module.css";

const LABELS: Record<TrackedJobStatus, string> = {
  pending: "Pending",
  validating: "Validating",
  ready: "Ready",
  failed: "Failed",
  not_found: "Not found",
};

export function StatusBadge({ status }: { status: TrackedJobStatus }) {
  return <span className={`${styles.badge} ${styles[status]}`}>{LABELS[status]}</span>;
}
