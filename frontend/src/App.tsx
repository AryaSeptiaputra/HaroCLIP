import { useState } from "react";
import { IngestionView } from "./IngestionView";
import { CampaignView } from "./CampaignView";
import "./App.css";

type Tab = "ingestion" | "campaign";

function App() {
  const [tab, setTab] = useState<Tab>("ingestion");

  return (
    <main className="app">
      <h1>HaroClip</h1>
      <nav className="tabs">
        <button
          className={`tab ${tab === "ingestion" ? "tabActive" : ""}`}
          type="button"
          onClick={() => setTab("ingestion")}
        >
          Ingestion
        </button>
        <button
          className={`tab ${tab === "campaign" ? "tabActive" : ""}`}
          type="button"
          onClick={() => setTab("campaign")}
        >
          Campaign Briefs
        </button>
      </nav>
      {tab === "ingestion" ? <IngestionView /> : <CampaignView />}
    </main>
  );
}

export default App;
