#!/bin/bash
# HaroClip — vast.ai "On-start Script"
#
# Runs every time the instance (re)starts. Fully idempotent — safe to run on a
# fresh instance, a restarted one, or one that's been stopped/started repeatedly.
# Prepares the environment so that once you SSH in, you can go straight to:
#   python3 -m src.pipeline.run --url "<video-url>"
# with no manual setup steps. See VAST_GUIDE.md for the full walkthrough this
# script automates, and for what to do if something here fails.
#
# Secrets are deliberately NOT set here: export ANTHROPIC_API_KEY and (optionally)
# HF_TOKEN via vast.ai's own "Environment Variables" field on the instance/template
# — never hardcode a real key value in this script, it's version-controlled.
#
# Optional: set ENABLE_UI=1 (same "Environment Variables" field) to also set up the
# browser UI's frontend (Node.js + npm install) — see step 3b below and
# VAST_GUIDE.md. Skipped by default since most sessions only need the CLI pipeline.

set -uo pipefail

REPO_URL="https://github.com/AryaSeptiaputra/HaroCLIP.git"      
REPO_DIR="$HOME/HaroCLIP"
BRANCH="vast-ai-e2e-prep"

echo "=== [entrypoint] $(date) starting ==="

# --- 1. Clone or update the repo ---
if [ -d "$REPO_DIR/.git" ]; then
    echo "[entrypoint] repo exists at $REPO_DIR, pulling latest $BRANCH..."
    cd "$REPO_DIR"
    git fetch origin
    git checkout "$BRANCH"
    git pull origin "$BRANCH"
else
    echo "[entrypoint] cloning $REPO_URL into $REPO_DIR..."
    git clone "$REPO_URL" "$REPO_DIR"
    cd "$REPO_DIR"
    git checkout "$BRANCH"
fi

# --- 2. System deps ---
if ! command -v ffmpeg &> /dev/null; then
    echo "[entrypoint] installing ffmpeg..."
    apt-get update && apt-get install -y ffmpeg
fi
# Real bold font for captioning's burned-in text (src/captioning/subtitles.py's
# ASS_TEMPLATE names "DejaVu Sans Bold" directly) — without it libass falls back
# to an uncontrolled default font.
if ! fc-list 2>/dev/null | grep -qi "DejaVu Sans"; then
    echo "[entrypoint] installing fonts-dejavu-core..."
    apt-get update && apt-get install -y fonts-dejavu-core
fi
if ! command -v sqlite3 &> /dev/null; then
    echo "[entrypoint] installing sqlite3 CLI..."
    apt-get update && apt-get install -y sqlite3
fi

# --- 3. Python deps ---
# torch is already installed by the PyTorch template — requirements.txt keeps it
# (and other heavy ML deps) commented out on purpose, so this stays a light install.
echo "[entrypoint] installing python deps..."
python3 -m pip install -r requirements.txt
python3 -m pip install --upgrade faster-whisper ultralytics supervision python_speech_features
python3 -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12

# --- 3b. Frontend UI setup (optional, only when ENABLE_UI=1) ---
# The UI is a convenience layer for the ingestion step (video link, campaign brief
# PDF, API keys) — most sessions only need the CLI pipeline, so this whole block is
# skipped unless explicitly opted into via vast.ai's own "Environment Variables"
# field (same place as ANTHROPIC_API_KEY/HF_TOKEN). Starting uvicorn/npm run dev
# themselves stays a manual step, deliberately not automated here — see
# VAST_GUIDE.md's "Using the browser UI on vast.ai" section for the exact commands
# and SSH tunnel setup.
if [ "${ENABLE_UI:-0}" = "1" ]; then
    if ! command -v node &> /dev/null; then
        echo "[entrypoint] ENABLE_UI=1: installing Node.js 20.x..."
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
        apt-get install -y nodejs
    fi
    if [ ! -d frontend/node_modules ]; then
        echo "[entrypoint] ENABLE_UI=1: installing frontend dependencies..."
        (cd frontend && npm install)
    fi
    if [ ! -f frontend/.env ]; then
        echo "[entrypoint] ENABLE_UI=1: creating frontend/.env from .env.example..."
        cp frontend/.env.example frontend/.env
    fi
    echo "[entrypoint] ENABLE_UI=1: frontend ready. To start the UI, in two separate"
    echo "[entrypoint]   shells: 'python3 -m uvicorn src.api.main:app --host 0.0.0.0"
    echo "[entrypoint]   --port 8000' and 'cd frontend && npm run dev' — then open an"
    echo "[entrypoint]   SSH tunnel from your local machine (see VAST_GUIDE.md)."
fi

# --- 4. cuBLAS/cuDNN fix (confirmed necessary on a real run — see VAST_GUIDE.md) ---
# Persisted to .bashrc too so it's still set in any interactive shell you open later.
CUBLAS_CUDNN_PATH=$(python3 -c "import os, nvidia.cublas.lib, nvidia.cudnn.lib; print(os.path.dirname(nvidia.cublas.lib.__file__) + ':' + os.path.dirname(nvidia.cudnn.lib.__file__))")
export LD_LIBRARY_PATH="$CUBLAS_CUDNN_PATH"
grep -qxF "export LD_LIBRARY_PATH=$CUBLAS_CUDNN_PATH" ~/.bashrc 2>/dev/null || echo "export LD_LIBRARY_PATH=$CUBLAS_CUDNN_PATH" >> ~/.bashrc

# --- 5. hf-xet workaround (confirmed necessary on a real run — see VAST_GUIDE.md) ---
export HF_HUB_DISABLE_XET=1
grep -qxF "export HF_HUB_DISABLE_XET=1" ~/.bashrc 2>/dev/null || echo "export HF_HUB_DISABLE_XET=1" >> ~/.bashrc

# --- 6. YOLOv8-face xlarge weights (manual prerequisite, not pip-installable) ---
mkdir -p data/models
if [ ! -f data/models/yolov8x-face-lindevs.pt ]; then
    echo "[entrypoint] downloading YOLOv8-face xlarge weights..."
    curl -L "https://github.com/lindevs/yolov8-face/releases/latest/download/yolov8x-face-lindevs.pt" \
        -o data/models/yolov8x-face-lindevs.pt
fi

# --- 7. DB migration (idempotent) ---
# This project has no migration system (init_db() is a bare
# Base.metadata.create_all(), which never adds columns to an existing table).
# Only touches an existing db+table that's actually missing the column; a brand
# new db gets the column for free from create_all(). Add future ALTER TABLE
# statements here the same way as new columns get introduced.
if [ -f data/haroclip.db ]; then
    if sqlite3 data/haroclip.db "SELECT name FROM sqlite_master WHERE type='table' AND name='highlight_jobs';" 2>/dev/null | grep -q highlight_jobs; then
        HAS_COLUMN=$(sqlite3 data/haroclip.db "PRAGMA table_info(highlight_jobs);" 2>/dev/null | grep -c "llm_response_path" || true)
        if [ "${HAS_COLUMN:-0}" -eq 0 ]; then
            echo "[entrypoint] migrating db: adding highlight_jobs.llm_response_path..."
            sqlite3 data/haroclip.db "ALTER TABLE highlight_jobs ADD COLUMN llm_response_path VARCHAR;"
        fi
    fi
    if sqlite3 data/haroclip.db "SELECT name FROM sqlite_master WHERE type='table' AND name='ingestion_jobs';" 2>/dev/null | grep -q ingestion_jobs; then
        HAS_CAMPAIGN_COLUMN=$(sqlite3 data/haroclip.db "PRAGMA table_info(ingestion_jobs);" 2>/dev/null | grep -c "campaign_context" || true)
        if [ "${HAS_CAMPAIGN_COLUMN:-0}" -eq 0 ]; then
            echo "[entrypoint] migrating db: adding ingestion_jobs.campaign_context..."
            sqlite3 data/haroclip.db "ALTER TABLE ingestion_jobs ADD COLUMN campaign_context TEXT;"
        fi
    fi
    if sqlite3 data/haroclip.db "SELECT name FROM sqlite_master WHERE type='table' AND name='ingestion_jobs';" 2>/dev/null | grep -q ingestion_jobs; then
        HAS_HF_TOKEN_COLUMN=$(sqlite3 data/haroclip.db "PRAGMA table_info(ingestion_jobs);" 2>/dev/null | grep -c "hf_token" || true)
        if [ "${HAS_HF_TOKEN_COLUMN:-0}" -eq 0 ]; then
            echo "[entrypoint] migrating db: adding ingestion_jobs.hf_token..."
            sqlite3 data/haroclip.db "ALTER TABLE ingestion_jobs ADD COLUMN hf_token TEXT;"
        fi
    fi
    if sqlite3 data/haroclip.db "SELECT name FROM sqlite_master WHERE type='table' AND name='highlight_clips';" 2>/dev/null | grep -q highlight_clips; then
        HAS_SEGMENTS_COLUMN=$(sqlite3 data/haroclip.db "PRAGMA table_info(highlight_clips);" 2>/dev/null | grep -c "segments_json" || true)
        if [ "${HAS_SEGMENTS_COLUMN:-0}" -eq 0 ]; then
            echo "[entrypoint] migrating db: adding highlight_clips.segments_json..."
            sqlite3 data/haroclip.db "ALTER TABLE highlight_clips ADD COLUMN segments_json TEXT;"
        fi
        # Backfill existing rows (added before jump-cut support) as a single-segment
        # list derived from their existing start_seconds/end_seconds, so old rows
        # remain readable by code that now expects segments_json to always be set.
        echo "[entrypoint] backfilling highlight_clips.segments_json for pre-existing rows..."
        sqlite3 data/haroclip.db "UPDATE highlight_clips SET segments_json = '[{\"start\": ' || start_seconds || ', \"end\": ' || end_seconds || '}]' WHERE segments_json IS NULL;"
    fi
    if sqlite3 data/haroclip.db "SELECT name FROM sqlite_master WHERE type='table' AND name='caption_jobs';" 2>/dev/null | grep -q caption_jobs; then
        HAS_ASS_COLUMN=$(sqlite3 data/haroclip.db "PRAGMA table_info(caption_jobs);" 2>/dev/null | grep -c "ass_path" || true)
        if [ "${HAS_ASS_COLUMN:-0}" -eq 0 ]; then
            HAS_SRT_COLUMN=$(sqlite3 data/haroclip.db "PRAGMA table_info(caption_jobs);" 2>/dev/null | grep -c "srt_path" || true)
            if [ "${HAS_SRT_COLUMN:-0}" -gt 0 ]; then
                echo "[entrypoint] migrating db: renaming caption_jobs.srt_path -> ass_path (karaoke .ass captions, 2026-08-01)..."
                sqlite3 data/haroclip.db "ALTER TABLE caption_jobs RENAME COLUMN srt_path TO ass_path;"
            fi
        fi
    fi
    if sqlite3 data/haroclip.db "SELECT name FROM sqlite_master WHERE type='table' AND name='processing_jobs';" 2>/dev/null | grep -q processing_jobs; then
        # yt-dlp metadata columns added 2026-08-01 (description/upload_date/uploader/platform).
        for col_spec in "description:TEXT" "upload_date:VARCHAR" "uploader:VARCHAR" "platform:VARCHAR"; do
            col_name="${col_spec%%:*}"
            col_type="${col_spec##*:}"
            HAS_COL=$(sqlite3 data/haroclip.db "PRAGMA table_info(processing_jobs);" 2>/dev/null | grep -c "$col_name" || true)
            if [ "${HAS_COL:-0}" -eq 0 ]; then
                echo "[entrypoint] migrating db: adding processing_jobs.$col_name..."
                sqlite3 data/haroclip.db "ALTER TABLE processing_jobs ADD COLUMN $col_name $col_type;"
            fi
        done
    fi
fi

# --- 8. Sanity checks ---
echo "[entrypoint] torch/cuda check:"
python3 -c "import torch; print(' torch', torch.__version__, 'cuda:', torch.cuda.is_available())"

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    echo "[entrypoint] ANTHROPIC_API_KEY is set."
else
    echo "[entrypoint] WARNING: ANTHROPIC_API_KEY is NOT set — set it in vast.ai's"
    echo "[entrypoint]          Environment Variables before running the pipeline,"
    echo "[entrypoint]          the highlights stage will fail without it."
fi

echo "=== [entrypoint] $(date) done — ready for: python3 -m src.pipeline.run --url <url> ==="
