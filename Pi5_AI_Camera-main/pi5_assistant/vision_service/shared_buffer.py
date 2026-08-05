"""Thread-safe shared buffer for the latest camera frame and detections.

The detection pipeline writes to this buffer continuously.
Tool handlers read from it instantly — no re-inference needed.
"""

import time
import threading
import numpy as np


class SharedDetectionBuffer:
    """Holds the latest frame and YOLO detections, thread-safe."""

    def __init__(self, max_age_ms: float = 1000):
        self._lock = threading.Lock()
        self._detections: list[dict] = []
        self._timestamp: float = 0.0
        self._latest_frame: np.ndarray | None = None
        self._max_age_ms = max_age_ms

    def update(self, detections: list[dict], frame: np.ndarray | None = None):
        """Called by the detection pipeline for every frame."""
        with self._lock:
            self._detections = detections
            self._timestamp = time.time()
            if frame is not None:
                self._latest_frame = frame

    def get(self) -> dict:
        """Read the latest detections (instant, no I/O)."""
        with self._lock:
            now = time.time()
            age_ms = (now - self._timestamp) * 1000
            return {
                "detections": self._detections.copy(),
                "timestamp": self._timestamp,
                "age_ms": round(age_ms, 1),
                "stale": age_ms > self._max_age_ms,
            }

    def get_frame(self) -> np.ndarray | None:
        """Get the latest camera frame (for VLM)."""
        with self._lock:
            return self._latest_frame.copy() if self._latest_frame is not None else None

    @property
    def is_populated(self) -> bool:
        with self._lock:
            return self._timestamp > 0
