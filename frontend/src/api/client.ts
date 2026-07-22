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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError("Could not reach the HaroClip API. Is the backend running?", "network", 0);
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") {
        detail = body.detail;
      } else if (Array.isArray(body?.detail)) {
        detail = body.detail.map((d: { msg?: string }) => d.msg).join("; ");
      }
    } catch {
      // response body wasn't JSON; fall back to statusText
    }
    throw new ApiError(detail, "http", response.status);
  }

  return response.json() as Promise<T>;
}

export function createIngestionJob(sourceUrl: string): Promise<IngestionJobRead> {
  const payload: IngestionJobCreate = { source_url: sourceUrl };
  return request<IngestionJobRead>("/ingestion/jobs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getIngestionJob(jobId: string): Promise<IngestionJobRead> {
  return request<IngestionJobRead>(`/ingestion/jobs/${jobId}`);
}
