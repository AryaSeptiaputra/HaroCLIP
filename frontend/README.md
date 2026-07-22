# HaroClip Frontend — Ingestion UI

React + Vite + TypeScript UI for the video ingestion module. Lets you submit a video link
(direct file URL or a platform link like YouTube/TikTok) to the ingestion API, and watch
its validation status resolve.

Reserved for future expansion into a fuller dashboard (campaign brief review, clip
preview/approval) — not built yet.

## Setup

```bash
npm install
cp .env.example .env   # defaults to http://localhost:8000
npm run dev
```

The ingestion backend (`src/api/main.py`) must be running separately on the URL configured
in `VITE_API_BASE_URL` (see repo root README/CLAUDE.md for how to start it), and must have
CORS enabled for the Vite dev server's origin (already configured for `localhost:5173`).

## What's here

- `src/api/` — typed fetch client for `POST /ingestion/jobs` and `GET /ingestion/jobs/{id}`.
- `src/hooks/useTrackedJobs.ts` — tracks submitted job IDs in `localStorage` (the backend has
  no list endpoint) and polls each non-terminal job every 3s until it reaches `ready`/`failed`.
- `src/components/` — `SubmitJobForm`, `JobList`/`JobListItem`, `JobDetail`, `StatusBadge`.
