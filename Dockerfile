# HaroClip — vast.ai deployment image
#
# Not a persistent service — this is built for a rented, SSH'd-into vast.ai instance:
# run one job, inspect data/logs|clips|reframed, download results, destroy the instance.
# No fixed ENTRYPOINT/CMD; see docs/hardware-spec.md for exact run commands.

FROM nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04

# --- Python 3.13 via deadsnakes (keeps parity with the documented dev environment) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3.13 python3.13-venv python3.13-dev \
        ffmpeg git build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.13 1 \
    && curl -sS https://bootstrap.pypa.io/get-pip.py | python3.13

WORKDIR /workspace

# --- Base dependencies (always installed) ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Heavy ML dependencies ---
# requirements.txt documents these as commented-out-pending-confirmation for local
# dev; this image *is* the confirmed production install, so they're installed
# explicitly here rather than by uncommenting requirements.txt (keeps that file as
# the single source of truth for "what needs confirming before a local install").
# Note: highlight detection uses the Claude API (anthropic, already in
# requirements.txt), not a local LLM — no transformers/accelerate/bitsandbytes needed.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cu124
RUN pip install --no-cache-dir \
        faster-whisper \
        ultralytics supervision python_speech_features

# --- YOLOv8-face weights, medium variant (the one weight file not already vendored in
# git — Light-ASD's weights are committed under src/detection/light_asd/weight/ and
# arrive via COPY below). Medium, not nano: VRAM freed by moving the highlight LLM to
# the Claude API went toward better face-detection accuracy instead. ---
RUN mkdir -p data/models && curl -L \
        "https://github.com/lindevs/yolov8-face/releases/latest/download/yolov8m-face-lindevs.pt" \
        -o data/models/yolov8m-face-lindevs.pt

COPY . .

ENV DATA_DIR=/workspace/data
ENV PYTHONUNBUFFERED=1

# No ENTRYPOINT/CMD — see docs/hardware-spec.md "Running via Docker" for the exact
# one-shot command (python3.13 -m src.pipeline.run --url <url>).
