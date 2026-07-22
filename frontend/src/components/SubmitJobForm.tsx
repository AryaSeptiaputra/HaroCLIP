import { useState } from "react";
import { ApiError, createIngestionJob } from "../api/client";
import styles from "./SubmitJobForm.module.css";

interface SubmitJobFormProps {
  onJobCreated: (jobId: string) => void;
}

export function SubmitJobForm({ onJobCreated }: SubmitJobFormProps) {
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;

    setSubmitting(true);
    setError(null);
    try {
      const job = await createIngestionJob(url.trim());
      onJobCreated(job.id);
      setUrl("");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Something went wrong submitting this link.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <label className={styles.label} htmlFor="video-url">
        Video link
      </label>
      <div className={styles.row}>
        <input
          id="video-url"
          className={styles.input}
          type="text"
          placeholder="https://... (direct video file or YouTube/TikTok/etc. link)"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={submitting}
        />
        <button className={styles.button} type="submit" disabled={submitting || !url.trim()}>
          {submitting ? "Submitting…" : "Submit"}
        </button>
      </div>
      {error && <p className={styles.error}>{error}</p>}
    </form>
  );
}
