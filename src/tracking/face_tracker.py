from src.detection.schemas import FaceBox
from src.tracking.schemas import TrackedFace


class FaceTracker:
    def __init__(self):
        import supervision as sv

        # supervision.ByteTrack — note: newer supervision releases deprecate
        # update_with_detections() in favor of a standalone `trackers` package's
        # update(); we're pinned to this API since it's what CLAUDE.md documents.
        self._tracker = sv.ByteTrack()
        self._sv = sv

    def update(self, frame_index: int, boxes: list[FaceBox]) -> list[TrackedFace]:
        import numpy as np

        if not boxes:
            detections = self._sv.Detections.empty()
        else:
            detections = self._sv.Detections(
                xyxy=np.array([[b.x1, b.y1, b.x2, b.y2] for b in boxes]),
                confidence=np.array([b.confidence for b in boxes]),
                class_id=np.zeros(len(boxes), dtype=int),
            )

        tracked = self._tracker.update_with_detections(detections)

        result: list[TrackedFace] = []
        for xyxy, confidence, track_id in zip(
            tracked.xyxy, tracked.confidence, tracked.tracker_id
        ):
            x1, y1, x2, y2 = (float(v) for v in xyxy)
            result.append(
                TrackedFace(
                    frame_index=frame_index,
                    track_id=int(track_id),
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    confidence=float(confidence),
                )
            )
        return result
