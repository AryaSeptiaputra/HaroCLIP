import { useRef, useState } from "react";
import { ApiError, createIngestionJob, uploadCampaignBrief } from "../api/client";
import styles from "./SubmitJobForm.module.css";

interface SubmitJobFormProps {
  onJobCreated: (jobId: string) => void;
}

export function SubmitJobForm({ onJobCreated }: SubmitJobFormProps) {
  const [url, setUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [hfToken, setHfToken] = useState("");
  const [briefFile, setBriefFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [briefStatus, setBriefStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;

    setSubmitting(true);
    setError(null);
    setBriefStatus(null);

    let jobId: string;
    try {
      const job = await createIngestionJob(url.trim(), { hfToken: hfToken.trim() || undefined });
      jobId = job.id;
      onJobCreated(jobId);
      setUrl("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong submitting this link.");
      setSubmitting(false);
      return;
    }

    if (briefFile) {
      setBriefStatus("Summarizing campaign brief…");
      try {
        await uploadCampaignBrief(jobId, briefFile, { apiKey: apiKey.trim() || undefined });
        setBriefStatus("Campaign brief applied.");
        setBriefFile(null);
        if (fileInputRef.current) fileInputRef.current.value = "";
      } catch (err) {
        const detail = err instanceof ApiError ? err.message : "Something went wrong summarizing the brief.";
        setBriefStatus(`Job ${jobId} was created, but the campaign brief failed: ${detail}`);
      }
    }

    setSubmitting(false);
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
      {briefStatus && <p className={styles.hint}>{briefStatus}</p>}

      <details className={styles.advanced}>
        <summary className={styles.advancedSummary}>Advanced options</summary>
        <div className={styles.advancedBody}>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="campaign-brief-pdf">
              Campaign brief (PDF)
            </label>
            <input
              id="campaign-brief-pdf"
              ref={fileInputRef}
              className={styles.fileInput}
              type="file"
              accept=".pdf"
              onChange={(e) => setBriefFile(e.target.files?.[0] ?? null)}
              disabled={submitting}
            />
            <p className={styles.help}>
              Summarized automatically by Claude and used to steer highlight selection.
            </p>
          </div>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="anthropic-api-key">
              Claude API key
            </label>
            <input
              id="anthropic-api-key"
              className={styles.input}
              type="password"
              placeholder="sk-ant-..."
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              disabled={submitting}
              autoComplete="off"
            />
            <p className={styles.help}>
              Only used when a campaign brief PDF is uploaded above — never saved.
            </p>
          </div>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="hf-token">
              Hugging Face token
            </label>
            <input
              id="hf-token"
              className={styles.input}
              type="password"
              placeholder="hf_..."
              value={hfToken}
              onChange={(e) => setHfToken(e.target.value)}
              disabled={submitting}
              autoComplete="off"
            />
            <p className={styles.help}>
              Saved with this job so the transcription stage can use it later — optional.
            </p>
          </div>
        </div>
      </details>
    </form>
  );
}
