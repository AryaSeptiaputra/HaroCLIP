# HaroClip Frontend — Campaign Brief UI

React + Vite + TypeScript UI for the campaign brief module. Lets you submit a campaign
brief — either pasted free text or an uploaded document (PDF/DOCX/TXT) — and browse
previously submitted briefs. Briefs are stored as-is; turning them into structured data
is a separate, future module.

Standalone from the ingestion module for now (no video linkage).

## Setup

```bash
npm install
cp .env.example .env   # defaults to http://localhost:8000
npm run dev
```

The campaign brief backend (`src/api/main.py` on this branch) must be running
separately on the URL configured in `VITE_API_BASE_URL`, with CORS enabled for the Vite
dev server's origin (already configured for `localhost:5173`).

## What's here

- `src/api/` — typed fetch client for `POST/GET /campaign/briefs` and
  `GET /campaign/briefs/{id}/file`.
- `src/hooks/useCampaignBriefs.ts` — fetches the brief list on mount and refetches after
  a successful submit (no polling — nothing async to wait on).
- `src/components/` — `SubmitBriefForm` (text/file mode toggle), `BriefList`/
  `BriefListItem`, `BriefDetail`.
