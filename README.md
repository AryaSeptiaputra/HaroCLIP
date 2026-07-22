# HaroClip

Automated video clip/highlight generator, built for creator/marketing campaign use cases
(including integration with Whop for campaign briefs).

## Tech Stack

- **faster-whisper** — speech-to-text
- **Light-ASD** — active speaker detection
- **YOLOv8-face** (ultralytics) — face detection
- **ByteTrack** — object/face tracking
- **ffmpeg** — video rendering/processing
- **FastAPI** — backend API (webhook ingestion, job orchestration)
- Python 3.13

Target hardware: single GPU workstation, 24GB VRAM budget.

## Project Status

Early setup phase — folder structure and environment scaffolding only. No pipeline
implementation yet. See `CLAUDE.md` for full context and next steps.

## Setup

### Prerequisites

- Python 3.13 (or compatible 3.x)
- [ffmpeg](https://ffmpeg.org/) available on PATH
- NVIDIA GPU + CUDA drivers (for detection/transcription models — not required for
  initial scaffolding)

### Environment

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Unix/macOS
source venv/bin/activate

pip install -r requirements.txt
```

> Note: `torch`/`torchvision` are intentionally commented out in `requirements.txt`.
> Install them separately with the CUDA build matching your GPU before installing
> `ultralytics` / `faster-whisper` dependents.

Copy `.env.example` to `.env` and fill in secrets (Whop API key, webhook secret, etc.)
before running the API server.

## Folder Structure

```
haroclip/
├── src/
│   ├── api/              # FastAPI app, Whop webhook, job orchestration
│   ├── ingestion/        # source video intake
│   ├── detection/        # highlight detection, YOLOv8-face, Light-ASD
│   ├── tracking/         # ByteTrack
│   ├── transcription/    # faster-whisper
│   ├── rendering/        # ffmpeg pipeline
│   ├── campaign/         # Whop integration, brief parsing -> JSON
│   └── utils/
├── frontend/             # placeholder for future dashboard UI
├── configs/              # model configs, thresholds, etc.
├── data/                 # sample/test data (gitignored)
├── models/               # model weights (gitignored)
├── scripts/              # CLI/dev scripts
├── tests/
├── docs/
├── requirements.txt
├── .env.example
├── README.md
└── CLAUDE.md
```
