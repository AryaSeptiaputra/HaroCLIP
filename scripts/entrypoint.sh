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
