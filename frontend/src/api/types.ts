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
  error_stage: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface IngestionJobCreate {
  source_url: string;
}

export type BriefContentType = "text" | "file";

export interface CampaignBriefRead {
  id: string;
  title: string;
  content_type: BriefContentType;
  raw_text: string | null;
  original_filename: string | null;
  mime_type: string | null;
  file_size_bytes: number | null;
  created_at: string;
  updated_at: string;
}
