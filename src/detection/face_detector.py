import logging
import os

import numpy as np

from src.detection.schemas import FaceBox
from src.utils.logging import log_vram

YOLOV8_FACE_WEIGHTS_PATH = os.getenv(
    "YOLOV8_FACE_WEIGHTS_PATH", "data/models/yolov8n-face-lindevs.pt"
)
CONFIDENCE_THRESHOLD = 0.5


class FaceDetector:
    def __init__(self, weights_path: str | None = None, logger: logging.Logger | None = None):
        from ultralytics import YOLO

        path = weights_path or YOLOV8_FACE_WEIGHTS_PATH
        if logger:
            logger.info("loading YOLOv8-face weights=%s", path)
        self._model = YOLO(path)
        if logger:
            log_vram(logger, "YOLOv8-face load")

    def detect(self, frame: np.ndarray, frame_index: int) -> list[FaceBox]:
        results = self._model.predict(frame, verbose=False)
        boxes: list[FaceBox] = []
        for result in results:
            for box in result.boxes:
                confidence = float(box.conf[0])
                if confidence < CONFIDENCE_THRESHOLD:
                    continue
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                boxes.append(
                    FaceBox(
                        frame_index=frame_index,
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2,
                        confidence=confidence,
                    )
                )
        return boxes

    def close(self) -> None:
        import torch

        del self._model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def __enter__(self) -> "FaceDetector":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
