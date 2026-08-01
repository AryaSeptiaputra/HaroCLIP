import type { IngestionJobCreate, IngestionJobRead } from "./types";

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

// Separate from request() above on purpose: a multipart body needs the browser to
// set its own Content-Type (with boundary), so it must NOT get the
// "application/json" header request() always attaches.
function requestForm<T>(path: string, formData: FormData): Promise<T> {
  return handleResponse<T>(() =>
    fetch(`${API_BASE_URL}${path}`, {
      method: "POST",
      body: formData,
    })
  );
}

export function createIngestionJob(
  sourceUrl: string,
  opts?: { hfToken?: string }
): Promise<IngestionJobRead> {
  const payload: IngestionJobCreate = { source_url: sourceUrl };
  if (opts?.hfToken) {
    payload.hf_token = opts.hfToken;
  }
  return request<IngestionJobRead>("/ingestion/jobs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getIngestionJob(jobId: string): Promise<IngestionJobRead> {
  return request<IngestionJobRead>(`/ingestion/jobs/${jobId}`);
}

export function uploadCampaignBrief(
  jobId: string,
  file: File,
  opts?: { apiKey?: string }
): Promise<IngestionJobRead> {
  const formData = new FormData();
  formData.append("file", file);
  if (opts?.apiKey) {
    formData.append("anthropic_api_key", opts.apiKey);
  }
  return requestForm<IngestionJobRead>(`/ingestion/jobs/${jobId}/campaign-brief`, formData);
}
