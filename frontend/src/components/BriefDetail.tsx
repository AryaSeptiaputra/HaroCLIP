import type { CampaignBriefRead } from "../api/types";
import { campaignBriefFileUrl } from "../api/client";
import styles from "./BriefDetail.module.css";

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function BriefDetail({ brief }: { brief: CampaignBriefRead | null }) {
  if (!brief) {
    return <p className={styles.empty}>Select a brief to see its details.</p>;
  }

  return (
    <div className={styles.panel}>
      <h3 className={styles.title}>{brief.title}</h3>
      <p className={styles.meta}>{new Date(brief.created_at).toLocaleString()}</p>

      {brief.content_type === "text" ? (
        <pre className={styles.text}>{brief.raw_text}</pre>
      ) : (
        <div className={styles.fileBox}>
          <dl className={styles.fileMeta}>
            <dt>Filename</dt>
            <dd>{brief.original_filename}</dd>
            <dt>Type</dt>
            <dd>{brief.mime_type ?? "—"}</dd>
            <dt>Size</dt>
            <dd>{formatBytes(brief.file_size_bytes)}</dd>
          </dl>
          <a className={styles.download} href={campaignBriefFileUrl(brief.id)} download>
            Download file
          </a>
        </div>
      )}
    </div>
  );
}
