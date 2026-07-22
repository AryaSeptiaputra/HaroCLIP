# HaroClip Frontend

React + Vite + TypeScript UI, currently covering two modules behind a top-level tab
switch in `App.tsx`:

- **Ingestion** — submit a video link (direct file URL or a platform link like
  YouTube/TikTok) and watch its validation status resolve.
- **Campaign Briefs** — submit a campaign brief (pasted free text or an uploaded
  PDF/DOCX/TXT document) and browse previously submitted briefs. Briefs are stored
  as-is; turning them into structured data is a separate, future module.

## Setup

```bash
npm install
cp .env.example .env   # defaults to http://localhost:8000
npm run dev
```

The unified backend (`src/api/main.py`, serving both `/ingestion/*` and
`/campaign/*`) must be running separately on the URL configured in
`VITE_API_BASE_URL`, with CORS enabled for the Vite dev server's origin (already
configured for `localhost:5173`).

## What's here

- `src/App.tsx` — top-level tab shell switching between `IngestionView` and
  `CampaignView`; no router, just local `useState` (revisit if a 3rd module or
  deep-linking makes a flat tab bar insufficient).
- `src/IngestionView.tsx` / `src/CampaignView.tsx` — each module's self-contained view
  (form + list + detail), each owning its own state.
- `src/api/` — typed fetch client covering both modules: `createIngestionJob`/
  `getIngestionJob` and `createCampaignBrief`/`listCampaignBriefs`/`getCampaignBrief`/
  `campaignBriefFileUrl`.
- `src/hooks/useTrackedJobs.ts` — tracks submitted ingestion job IDs in `localStorage`
  (the ingestion backend has no list endpoint) and polls each non-terminal job every 3s
  until it reaches `ready`/`failed`.
- `src/hooks/useCampaignBriefs.ts` — fetches the brief list on mount and refetches after
  a successful submit (no polling — nothing async to wait on).
- `src/components/` — `SubmitJobForm`, `JobList`/`JobListItem`, `JobDetail`,
  `StatusBadge` (ingestion) and `SubmitBriefForm`, `BriefList`/`BriefListItem`,
  `BriefDetail` (campaign briefs).
