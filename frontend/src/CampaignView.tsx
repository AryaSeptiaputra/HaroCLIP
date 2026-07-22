import { useState } from "react";
import { SubmitBriefForm } from "./components/SubmitBriefForm";
import { BriefList } from "./components/BriefList";
import { BriefDetail } from "./components/BriefDetail";
import { useCampaignBriefs } from "./hooks/useCampaignBriefs";

export function CampaignView() {
  const { briefs, loading, error, refresh } = useCampaignBriefs();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const selectedBrief = briefs.find((b) => b.id === selectedId) ?? null;

  const handleBriefCreated = (id: string) => {
    refresh();
    setSelectedId(id);
  };

  return (
    <>
      <SubmitBriefForm onBriefCreated={handleBriefCreated} />
      <div className="layout">
        <section>
          <h2>Briefs</h2>
          {loading && <p className="hint">Loading…</p>}
          {error && <p className="hint error">{error}</p>}
          {!loading && !error && (
            <BriefList briefs={briefs} selectedId={selectedId} onSelect={setSelectedId} />
          )}
        </section>
        <section>
          <h2>Details</h2>
          <BriefDetail brief={selectedBrief} />
        </section>
      </div>
    </>
  );
}
