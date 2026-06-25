"""Continuous real-time object detection on Hailo-10H.

Runs in a dedicated background thread. Writes results to SharedDetectionBuffer.
Camera Module 3 → Hailo-10H YOLO pipeline → shared_buffer.

Supports dynamic FPS/resolution adjustment:
- Detects motion by frame differencing
- High motion → lower res + higher FPS (e.g. 640×320 @ 60fps)
- Low motion  → full res + normal FPS (e.g. 640×640 @ 30fps)
"""

import time
import threading
import logging
from collections import deque
import cv2
import numpy as np

logger = logging.getLogger(__name__)


class DetectionPipeline:
    """Continuous YOLO detection on Hailo-10H with dynamic motion adjustment."""

    def __init__(self, shared_buffer, config: dict):
        self.shared_buffer = shared_buffer
        self.config = config
        self._running = False
        self._thread: threading.Thread | None = None
        self._fps = 0.0

        # Dynamic adjustment state
        da = config["camera"].get("dynamic_adjust", {})
        self._dynamic_enabled = da.get("enabled", False)
        self._prev_gray = None
        self._motion_scores = deque(maxlen=da.get("motion_window", 5))
        self._last_adjust_time = 0.0
        self._check_interval = da.get("check_interval", 0.5)
        self._motion_threshold = da.get("motion_threshold", 30)
        self._current_profile = "low_motion"

    def start(self):
        """Start the detection thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Detection pipeline started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        """Main loop — captures frames, runs inference, updates buffer."""
        if self.config["detection"]["backend"] == "hailo":
            self._run_hailo()
        else:
            self._run_fallback()

    def _resolve_camera_config(self):
        """Return current (width, height, framerate) based on motion profile."""
        if not self._dynamic_enabled:
            res = self.config["camera"]["base_resolution"]
            fps = self.config["camera"]["base_framerate"]
            return res[0], res[1], fps

        profiles = self.config["camera"]["dynamic_adjust"]["profiles"]
        profile = profiles.get(self._current_profile, profiles["low_motion"])
        w, h = profile["resolution"]
        fps = profile["framerate"]
        return w, h, fps

    def _estimate_motion(self, frame: np.ndarray) -> float:
        """Compute mean absolute difference between current and previous frame."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self._prev_gray is None:
            self._prev_gray = gray
            return 0.0
        diff = cv2.absdiff(gray, self._prev_gray)
        self._prev_gray = gray
        return float(np.mean(diff))

    def _maybe_adjust_profile(self, frame: np.ndarray, now: float):
        """Check motion and switch camera profile if needed."""
        if not self._dynamic_enabled:
            return
        if now - self._last_adjust_time < self._check_interval:
            return

        # Use a downscaled frame for faster motion estimation
        small = cv2.resize(frame, (160, 120))
        score = self._estimate_motion(small)
        self._motion_scores.append(score)
        avg_motion = np.mean(self._motion_scores)

        new_profile = "high_motion" if avg_motion > self._motion_threshold else "low_motion"
        if new_profile != self._current_profile:
            self._current_profile = new_profile
            self._last_adjust_time = now
            label = self.config["camera"]["dynamic_adjust"]["profiles"][new_profile]["label"]
            w, h, fps = self._resolve_camera_config()
            self._reconfigure_camera(w, h, fps)
            logger.info(f"Motion profile → {label} ({w}×{h} @ {fps}fps, motion={avg_motion:.1f})")

        self._last_adjust_time = now

    def _reconfigure_camera(self, width: int, height: int, fps: int):
        """Dynamically change camera capture parameters."""
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_FPS, fps)
        # Flush buffer so next frames use the new config
        for _ in range(5):
            self._cap.grab()

    def _run_hailo(self):
        """Hailo-10H accelerated pipeline with dynamic FPS/resolution."""
        import hailo  # HailoRT Python bindings
        import cv2

        # Configure Hailo device
        device = hailo.Device()
        hef_path = f"/usr/share/hailo-models/{self.config['detection']['model']}.hef"
        network_group = device.configure(hef_path)[0]
        network_group.activate()

        # Configure camera
        w, h, fps = self._resolve_camera_config()
        self._cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self._cap.set(cv2.CAP_PROP_FPS, fps)

        # YOLO always runs at its trained resolution
        yolo_w, yolo_h = self.config["camera"]["base_resolution"]

        frame_count = 0
        fps_timer = time.time()

        while self._running:
            ret, frame = self._cap.read()
            if not ret:
                continue

            now = time.time()

            # Dynamic motion check
            self._maybe_adjust_profile(frame, now)

            # Resize captured frame to YOLO input size
            resized = cv2.resize(frame, (yolo_w, yolo_h))
            input_tensor = np.expand_dims(resized.transpose(2, 0, 1), 0).astype(np.float32)

            # Infer on Hailo-10H
            output = network_group.run(input_tensor)[0]

            # Post-process (NMS, threshold)
            detections = self._postprocess(output, yolo_w, yolo_h)

            # Update shared buffer with original frame + detections
            self.shared_buffer.update(detections, frame)

            # FPS tracking
            frame_count += 1
            if now - fps_timer >= 1.0:
                self._fps = frame_count / (now - fps_timer)
                frame_count = 0
                fps_timer = now

        self._cap.release()

    def _postprocess(self, output, width, height) -> list[dict]:
        """Convert raw Hailo output to structured detections."""
        conf_threshold = self.config["detection"]["confidence"]
        iou_threshold = self.config["detection"]["iou_threshold"]

        boxes, scores, class_ids = [], [], []
        # Hailo output parsing — model-specific
        # This is a simplified example; actual parsing depends on the .hef
        detections = []
        for detection in output:
            if len(detection) >= 6:
                x1, y1, x2, y2, score, class_id = detection[:6]
                if score >= conf_threshold:
                    detections.append({
                        "class": int(class_id),
                        "confidence": float(score),
                        "bbox": [int(x1), int(y1), int(x2 - x1), int(y2 - y1)],
                    })
        return detections

    def _run_fallback(self):
        """CPU-only fallback using OpenCV DNN with dynamic adjustment."""
        import cv2
        w, h, fps = self._resolve_camera_config()
        self._cap = cv2.VideoCapture(0)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self._cap.set(cv2.CAP_PROP_FPS, fps)

        net = cv2.dnn.readNet(
            f"{self.config['detection']['model']}.weights",
            f"{self.config['detection']['model']}.cfg"
        )
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

        yolo_w, yolo_h = self.config["camera"]["base_resolution"]

        while self._running:
            ret, frame = self._cap.read()
            if not ret:
                continue

            now = time.time()
            self._maybe_adjust_profile(frame, now)

            blob = cv2.dnn.blobFromImage(frame, 1/255.0, (yolo_w, yolo_h), swapRB=True)
            net.setInput(blob)
            output = net.forward()
            self.shared_buffer.update(self._postprocess_yolo(output, yolo_w, yolo_h), frame)

        self._cap.release()

    @property
    def fps(self) -> float:
        return self._fps
