export type LinkType = "direct" | "platform";

export type JobStatus = "pending" | "validating" | "ready" | "failed";

export interface IngestionJobRead {
  id: string;
  source_url: string;
  link_type: LinkType | null;
  status: JobStatus;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  video_codec: string | null;
  audio_codec: string | null;
  title: string | null;
  campaign_context: string | null;
  error_stage: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface IngestionJobCreate {
  source_url: string;
  campaign_context?: string;
  // Write-only on the backend (src/ingestion/schemas.py) — never comes back in
  // IngestionJobRead, so there's no field for it above.
  hf_token?: string;
}
