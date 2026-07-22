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
