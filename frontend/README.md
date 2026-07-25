# HaroClip Frontend

React + Vite + TypeScript UI for the ingestion module (the only module with a frontend
right now — UI work is paused for later backend modules until they're all done).

- **Ingestion** — submit a video link (direct file URL or a platform link like
  YouTube/TikTok) and watch its validation status resolve.

## Setup

```bash
npm install
cp .env.example .env   # defaults to http://localhost:8000
npm run dev
```

The backend (`src/api/main.py`, serving `/ingestion/*`) must be running separately on
the URL configured in `VITE_API_BASE_URL`, with CORS enabled for the Vite dev server's
origin (already configured for `localhost:5173`).

## What's here

- `src/App.tsx` — renders `IngestionView` directly (no tab shell — that was removed
  along with the campaign briefs module, which was the only other view).
- `src/IngestionView.tsx` — the module's self-contained view (form + list + detail),
  owning its own state.
- `src/api/` — typed fetch client: `createIngestionJob`/`getIngestionJob`.
- `src/hooks/useTrackedJobs.ts` — tracks submitted ingestion job IDs in `localStorage`
  (the ingestion backend has no list endpoint) and polls each non-terminal job every 3s
  until it reaches `ready`/`failed`.
- `src/components/` — `SubmitJobForm`, `JobList`/`JobListItem`, `JobDetail`,
  `StatusBadge`.
