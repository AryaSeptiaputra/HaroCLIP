import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getIngestionJob } from "../api/client";
import type { IngestionJobRead } from "../api/types";

const STORAGE_KEY = "haroclip.ingestion.jobIds";
const POLL_INTERVAL_MS = 3000;

export type TrackedJobStatus = IngestionJobRead["status"] | "not_found";

export interface TrackedJob {
  id: string;
  job: IngestionJobRead | null;
  status: TrackedJobStatus;
}

function readJobIds(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : [];
  } catch {
    return [];
  }
}

function writeJobIds(ids: string[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
}

function isTerminal(status: TrackedJobStatus): boolean {
  return status === "ready" || status === "failed" || status === "not_found";
}

export function useTrackedJobs() {
  const [jobIds, setJobIds] = useState<string[]>(() => readJobIds());
  const [jobsById, setJobsById] = useState<Record<string, IngestionJobRead>>({});
  const [notFoundIds, setNotFoundIds] = useState<Set<string>>(new Set());
  const pollingRef = useRef<{ jobIds: string[]; jobsById: Record<string, IngestionJobRead>; notFoundIds: Set<string> }>({
    jobIds,
    jobsById: {},
    notFoundIds: new Set(),
  });

  pollingRef.current.jobIds = jobIds;
  pollingRef.current.jobsById = jobsById;
  pollingRef.current.notFoundIds = notFoundIds;

  useEffect(() => {
    const tick = async () => {
      const { jobIds: currentIds, jobsById: currentJobs, notFoundIds: currentNotFound } = pollingRef.current;
      const pending = currentIds.filter((id) => {
        if (currentNotFound.has(id)) return false;
        const known = currentJobs[id];
        return !known || !isTerminal(known.status);
      });
      if (pending.length === 0) return;

      const results = await Promise.allSettled(pending.map((id) => getIngestionJob(id)));
      let jobsChanged = false;
      const nextJobs = { ...currentJobs };
      const nextNotFound = new Set(currentNotFound);

      results.forEach((result, i) => {
        const id = pending[i];
        if (result.status === "fulfilled") {
          nextJobs[id] = result.value;
          jobsChanged = true;
        } else if (result.reason instanceof ApiError && result.reason.status === 404) {
          nextNotFound.add(id);
        }
        // other failures (network) are left as-is; next tick retries
      });

      if (jobsChanged) setJobsById(nextJobs);
      if (nextNotFound.size !== currentNotFound.size) setNotFoundIds(nextNotFound);
    };

    tick();
    const interval = setInterval(tick, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  const addJobId = useCallback((id: string) => {
    setJobIds((prev) => {
      const next = [id, ...prev.filter((existing) => existing !== id)];
      writeJobIds(next);
      return next;
    });
  }, []);

  const removeJobId = useCallback((id: string) => {
    setJobIds((prev) => {
      const next = prev.filter((existing) => existing !== id);
      writeJobIds(next);
      return next;
    });
    setJobsById((prev) => {
      const rest = { ...prev };
      delete rest[id];
      return rest;
    });
    setNotFoundIds((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);

  const trackedJobs: TrackedJob[] = jobIds.map((id) => ({
    id,
    job: jobsById[id] ?? null,
    status: notFoundIds.has(id) ? "not_found" : jobsById[id]?.status ?? "pending",
  }));

  return { trackedJobs, addJobId, removeJobId };
}
