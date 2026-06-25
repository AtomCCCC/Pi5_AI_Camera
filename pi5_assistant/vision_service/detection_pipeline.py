"""Continuous real-time object detection on Hailo-10H.

Runs in a dedicated background thread. Writes results to SharedDetectionBuffer.
Camera Module 3 → Hailo-10H YOLO pipeline → shared_buffer.
"""

import time
import threading
import logging
import numpy as np

logger = logging.getLogger(__name__)


class DetectionPipeline:
    """Continuous YOLO detection on Hailo-10H."""

    def __init__(self, shared_buffer, config: dict):
        self.shared_buffer = shared_buffer
        self.config = config
        self._running = False
        self._thread: threading.Thread | None = None
        self._fps = 0.0

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

    def _run_hailo(self):
        """Hailo-10H accelerated pipeline."""
        import hailo  # HailoRT Python bindings
        import cv2

        # Configure Hailo device
        device = hailo.Device()
        hef_path = f"/usr/share/hailo-models/{self.config['detection']['model']}.hef"
        network_group = device.configure(hef_path)[0]
        network_group.activate()

        # Configure camera
        w, h = self.config["camera"]["resolution"]
        cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        cap.set(cv2.CAP_PROP_FPS, self.config["camera"]["framerate"])

        frame_count = 0
        fps_timer = time.time()

        while self._running:
            ret, frame = cap.read()
            if not ret:
                continue

            # Preprocess
            resized = cv2.resize(frame, (w, h))
            input_tensor = np.expand_dims(resized.transpose(2, 0, 1), 0).astype(np.float32)

            # Infer on Hailo-10H
            output = network_group.run(input_tensor)[0]

            # Post-process (NMS, threshold)
            detections = self._postprocess(output, w, h)

            # Update shared buffer
            self.shared_buffer.update(detections, frame)

            # FPS tracking
            frame_count += 1
            if time.time() - fps_timer >= 1.0:
                self._fps = frame_count / (time.time() - fps_timer)
                frame_count = 0
                fps_timer = time.time()

        cap.release()

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
        """CPU-only fallback using OpenCV DNN."""
        import cv2
        w, h = self.config["camera"]["resolution"]
        cap = cv2.VideoCapture(0)
        net = cv2.dnn.readNet(
            f"{self.config['detection']['model']}.weights",
            f"{self.config['detection']['model']}.cfg"
        )
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

        while self._running:
            ret, frame = cap.read()
            if not ret:
                continue
            blob = cv2.dnn.blobFromImage(frame, 1/255.0, (w, h), swapRB=True)
            net.setInput(blob)
            output = net.forward()
            # Post-process and update buffer
            self.shared_buffer.update(self._postprocess_yolo(output, w, h), frame)

        cap.release()

    @property
    def fps(self) -> float:
        return self._fps
