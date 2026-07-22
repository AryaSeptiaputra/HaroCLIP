import type { CampaignBriefRead } from "../api/types";
import { BriefListItem } from "./BriefListItem";
import styles from "./BriefList.module.css";

interface BriefListProps {
  briefs: CampaignBriefRead[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function BriefList({ briefs, selectedId, onSelect }: BriefListProps) {
  if (briefs.length === 0) {
    return <p className={styles.empty}>No briefs submitted yet.</p>;
  }

  return (
    <ul className={styles.list}>
      {briefs.map((brief) => (
        <BriefListItem
          key={brief.id}
          brief={brief}
          selected={brief.id === selectedId}
          onSelect={onSelect}
        />
      ))}
    </ul>
  );
}
