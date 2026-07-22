import type { CampaignBriefRead } from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export type ApiErrorKind = "network" | "http";

export class ApiError extends Error {
  kind: ApiErrorKind;
  status: number;

  constructor(message: string, kind: ApiErrorKind, status: number) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

async function parseErrorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") {
      return body.detail;
    }
    if (Array.isArray(body?.detail)) {
      return body.detail.map((d: { msg?: string }) => d.msg).join("; ");
    }
  } catch {
    // response body wasn't JSON; fall back to statusText
  }
  return response.statusText;
}

async function handleResponse<T>(fetchCall: () => Promise<Response>): Promise<T> {
  let response: Response;
  try {
    response = await fetchCall();
  } catch {
    throw new ApiError("Could not reach the HaroClip API. Is the backend running?", "network", 0);
  }

  if (!response.ok) {
    throw new ApiError(await parseErrorDetail(response), "http", response.status);
  }

  return response.json() as Promise<T>;
}

function request<T>(path: string, init?: RequestInit): Promise<T> {
  return handleResponse<T>(() =>
    fetch(`${API_BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    })
  );
}

function requestMultipart<T>(path: string, init: RequestInit): Promise<T> {
  return handleResponse<T>(() => fetch(`${API_BASE_URL}${path}`, init));
}

export function createCampaignBrief(
  title: string,
  rawText: string | null,
  file: File | null
): Promise<CampaignBriefRead> {
  const form = new FormData();
  form.append("title", title);
  if (rawText != null) form.append("raw_text", rawText);
  if (file != null) form.append("file", file);

  return requestMultipart<CampaignBriefRead>("/campaign/briefs", {
    method: "POST",
    body: form,
  });
}

export function listCampaignBriefs(): Promise<CampaignBriefRead[]> {
  return request<CampaignBriefRead[]>("/campaign/briefs");
}

export function getCampaignBrief(id: string): Promise<CampaignBriefRead> {
  return request<CampaignBriefRead>(`/campaign/briefs/${id}`);
}

export function campaignBriefFileUrl(id: string): string {
  return `${API_BASE_URL}/campaign/briefs/${id}/file`;
}
