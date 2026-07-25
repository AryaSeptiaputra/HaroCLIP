import logging

from src.utils.db import DATA_DIR

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"


def get_job_logger(stage: str, ingestion_job_id: str) -> logging.Logger:
    """Logger for one pipeline stage's run against one ingestion job.

    Writes to both stdout (visible live over SSH) and
    data/logs/<ingestion_job_id>/<stage>.log (a real file the user can collect and
    hand back for analysis, not scrollback to copy-paste).
    """
    logger = logging.getLogger(f"haroclip.{stage}.{ingestion_job_id}")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter(LOG_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    log_dir = DATA_DIR / "logs" / ingestion_job_id
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_dir / f"{stage}.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def log_vram(logger: logging.Logger, label: str) -> None:
    """Logs current CUDA VRAM usage, if CUDA is available. No-ops otherwise (e.g.
    local CPU verification) — used to compare real usage against docs/hardware-spec.md
    estimates.
    """
    try:
        import torch
    except ImportError:
        return

    if not torch.cuda.is_available():
        return

    allocated = torch.cuda.memory_allocated() / (1024**2)
    reserved = torch.cuda.memory_reserved() / (1024**2)
    logger.info("VRAM after %s: allocated=%.1fMB reserved=%.1fMB", label, allocated, reserved)
