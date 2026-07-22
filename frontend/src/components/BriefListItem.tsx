import type { CampaignBriefRead } from "../api/types";
import styles from "./BriefListItem.module.css";

interface BriefListItemProps {
  brief: CampaignBriefRead;
  selected: boolean;
  onSelect: (id: string) => void;
}

export function BriefListItem({ brief, selected, onSelect }: BriefListItemProps) {
  return (
    <li className={`${styles.item} ${selected ? styles.selected : ""}`}>
      <button className={styles.select} type="button" onClick={() => onSelect(brief.id)}>
        <span className={styles.title}>{brief.title}</span>
        <span className={`${styles.badge} ${styles[brief.content_type]}`}>
          {brief.content_type === "file" ? "File" : "Text"}
        </span>
      </button>
    </li>
  );
}
