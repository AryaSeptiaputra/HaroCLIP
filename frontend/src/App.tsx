import { useState } from "react";
import { SubmitJobForm } from "./components/SubmitJobForm";
import { JobList } from "./components/JobList";
import { JobDetail } from "./components/JobDetail";
import { useTrackedJobs } from "./hooks/useTrackedJobs";
import "./App.css";

function App() {
  const { trackedJobs, addJobId, removeJobId } = useTrackedJobs();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const selectedJob = trackedJobs.find((t) => t.id === selectedId) ?? null;

  const handleRemove = (id: string) => {
    removeJobId(id);
    if (selectedId === id) setSelectedId(null);
  };

  const handleJobCreated = (id: string) => {
    addJobId(id);
    setSelectedId(id);
  };

  return (
    <main className="app">
      <h1>HaroClip Ingestion</h1>
      <SubmitJobForm onJobCreated={handleJobCreated} />
      <div className="layout">
        <section>
          <h2>Jobs</h2>
          <JobList
            trackedJobs={trackedJobs}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onRemove={handleRemove}
          />
        </section>
        <section>
          <h2>Details</h2>
          <JobDetail trackedJob={selectedJob} />
        </section>
      </div>
    </main>
  );
}

export default App;
