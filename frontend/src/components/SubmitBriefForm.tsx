import { useState } from "react";
import { ApiError, createCampaignBrief } from "../api/client";
import styles from "./SubmitBriefForm.module.css";

type Mode = "text" | "file";

interface SubmitBriefFormProps {
  onBriefCreated: (briefId: string) => void;
}

export function SubmitBriefForm({ onBriefCreated }: SubmitBriefFormProps) {
  const [title, setTitle] = useState("");
  const [mode, setMode] = useState<Mode>("text");
  const [rawText, setRawText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const switchMode = (next: Mode) => {
    setMode(next);
    if (next === "text") setFile(null);
    else setRawText("");
  };

  const canSubmit = title.trim() && (mode === "text" ? rawText.trim() : file != null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;

    setSubmitting(true);
    setError(null);
    try {
      const brief = await createCampaignBrief(
        title.trim(),
        mode === "text" ? rawText.trim() : null,
        mode === "file" ? file : null
      );
      onBriefCreated(brief.id);
      setTitle("");
      setRawText("");
      setFile(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong submitting this brief.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <label className={styles.label} htmlFor="brief-title">
        Campaign / client title
      </label>
      <input
        id="brief-title"
        className={styles.input}
        type="text"
        placeholder="Campaign X - Client Y"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        disabled={submitting}
      />

      <div className={styles.modeTabs}>
        <button
          type="button"
          className={`${styles.modeTab} ${mode === "text" ? styles.modeTabActive : ""}`}
          onClick={() => switchMode("text")}
          disabled={submitting}
        >
          Paste text
        </button>
        <button
          type="button"
          className={`${styles.modeTab} ${mode === "file" ? styles.modeTabActive : ""}`}
          onClick={() => switchMode("file")}
          disabled={submitting}
        >
          Upload file
        </button>
      </div>

      {mode === "text" ? (
        <textarea
          className={styles.textarea}
          placeholder="Paste the brief content here..."
          value={rawText}
          onChange={(e) => setRawText(e.target.value)}
          disabled={submitting}
          rows={6}
        />
      ) : (
        <input
          className={styles.input}
          type="file"
          accept=".pdf,.docx,.txt"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          disabled={submitting}
        />
      )}

      <button className={styles.button} type="submit" disabled={submitting || !canSubmit}>
        {submitting ? "Submitting…" : "Submit brief"}
      </button>

      {error && <p className={styles.error}>{error}</p>}
    </form>
  );
}
