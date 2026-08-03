"""Continuous real-time object detection on Hailo-10H.

Camera Module 3 → Hailo-10H YOLO pipeline → shared_buffer.

Camera capture uses rpicam-jpeg (subprocess) since OpenCV cv2.VideoCapture
does not work with Pi Camera Module 3's libcamera backend.
"""

import time
import cv2
import json
import subprocess as sp
import numpy as np
import base64
import logging
import threading
from pathlib import Path

# KAN: adaptive controller utilities
try:
    from vision_service.student_kan_new import load_kan, compute_s_id, kan_infer
except ImportError:
    from student_kan_new import load_kan, compute_s_id, kan_infer


logger = logging.getLogger(__name__)


class DetectionPipeline:
    def __init__(self, config, shared_buffer, mqtt_client=None):
        self.config = config
        self.shared_buffer = shared_buffer
        self.mqtt_client = mqtt_client
        self._configured_model = None
        self._vdevice = None
        self._output_buffers = {}
        self._model_w = 0
        self._model_h = 0

        camera_cfg = config["camera"]
        dynamic_cfg = camera_cfg["dynamic_adjust"]

        self.enabled = dynamic_cfg.get("enabled", True)
        self.check_interval = dynamic_cfg.get("check_interval", 0.5)
        self.motion_threshold = dynamic_cfg.get("motion_threshold", 30)
        self.profiles = dynamic_cfg.get("profiles", {})

        self.current_profile = "low_motion"
        self.last_check_time = 0
        self.previous_gray = None
        self._running = False
        self._thread = None
        self._last_frame_publish = 0.0
        self.frame_publish_interval = config["mqtt"].get("frame_publish_interval", 0.2)

        self.topic_fps_status = config["mqtt"].get(
            "topic_fps_status",
            "vision/fps/status"
        )

        # KAN: load the adaptive controller (pure numpy, no torch)
        kan_weights = Path(__file__).with_name("kan_weights.npz")
        self.kan = load_kan(str(kan_weights))
        self.last_s_id = 0.0
        self.last_alpha = 0.0
        self.target_classes = [] 
        
    def set_target_classes(self, classes):
         self.target_classes = list(classes)

    def _init_hailo(self):
        """Lazy-init Hailo-10H infer model (called once on first process_frame)."""
        if self._configured_model is not None:
            return

        from hailo_platform import VDevice, HailoSchedulingAlgorithm

        hef_path = "/usr/local/hailo/resources/models/hailo10h/hailo_yolov8n_4_classes_vga.hef"

        params = VDevice.create_params()
        params.scheduling_algorithm = HailoSchedulingAlgorithm.ROUND_ROBIN
        params.group_id = "SHARED"
        self._vdevice = VDevice(params)

        infer_model = self._vdevice.create_infer_model(hef_path)
        infer_model.set_batch_size(1)

        input_stream = infer_model.inputs[0]
        self._model_h, self._model_w, _ = input_stream.shape

        for s in infer_model.outputs:
            self._output_buffers[s.name] = np.empty(s.shape, dtype=np.float32)

        self._configured_model = infer_model.configure()

    def estimate_motion(self, frame):
        small = cv2.resize(frame, (160, 120))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        if self.previous_gray is None:
            self.previous_gray = gray
            return 0

        diff = cv2.absdiff(self.previous_gray, gray)
        motion_score = diff.mean()
        self.previous_gray = gray

        return motion_score
        
     def select_profile(self, detections, frame_w, frame_h):
        # KAN-based decision with hysteresis
        if self.target_classes:
            detections = [d for d in detections
                          if d.get("name") in self.target_classes]
        s_id = compute_s_id(detections, frame_w, frame_h)
        delta_s_id = abs(s_id - self.last_s_id)
        self.last_s_id = s_id

        if self.kan is None:
            return self.current_profile

        alpha = kan_infer(self.kan, s_id, delta_s_id)
        self.last_alpha = alpha        # store for display

        # Hysteresis: only switch when alpha clearly crosses a threshold;
        # hold the current profile in the 0.4-0.6 dead band to avoid flapping
        if alpha > 0.6:
            self.current_profile = "high_motion"
        elif alpha < 0.4:
            self.current_profile = "low_motion"
        return self.current_profile
         
    def _capture_frame(self, w, h):
        """Capture a single frame via rpicam-jpeg."""
        result = sp.run(
            ["rpicam-jpeg", "--output", "-", "--width", str(w), "--height", str(h),
             "--nopreview", "--timeout", "100"],
            capture_output=True, timeout=5
        )
        if len(result.stdout) < 100:
            return None
        return cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_COLOR)

    def _infer(self, frame):
        """Run Hailo YOLO inference on a frame. Returns list of detections."""
        self._init_hailo()

        resized = cv2.resize(frame, (self._model_w, self._model_h))
        input_batch = np.expand_dims(resized.astype(np.uint8), 0)

        out = {name: np.empty(buf.shape, dtype=buf.dtype) for name, buf in self._output_buffers.items()}
        b = self._configured_model.create_bindings(output_buffers=out)
        b.input().set_buffer(np.array(input_batch))
        self._configured_model.run([b], timeout=10000)

        raw = out[list(out.keys())[0]]
        return self._postprocess(raw)

    def _postprocess(self, raw: np.ndarray) -> list[dict]:
        """Parse HAILO_NMS_BY_CLASS output from hailo_yolov8n_4_classes_vga."""
        conf_threshold = self.config["detection"]["confidence"]
        class_names = ["person", "car", "bicycle", "motorcycle"]

        data = raw.ravel()
        idx = 0
        detections = []
        for class_id in range(4):
            count = int(data[idx])
            idx += 1
            if count <= 0:
                continue
            bboxes = data[idx:idx + count * 5].reshape(-1, 5)
            idx += count * 5
            for x1, y1, x2, y2, conf in bboxes:
                if conf >= conf_threshold:
                    detections.append({
                        "class": class_id,
                        "name": class_names[class_id],
                        "confidence": round(float(conf), 3),
                        "bbox": [int(x1), int(y1), int(x2 - x1), int(y2 - y1)],
                    })
        return detections

    def start(self):
        """Start continuous capture and inference in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="vision-pipeline", daemon=True
        )
        self._thread.start()

    def stop(self):
        """Stop the continuous capture loop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=6)

    def _run_loop(self):
        while self._running:
            started = time.monotonic()
            try:
                self.process_frame()
            except Exception:
                logger.exception("Vision frame processing failed")
                time.sleep(1)
            else:
                # Avoid a tight retry loop if camera capture returns immediately.
                remaining = 0.01 - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(remaining)

    def _publish_annotated_frame(self, frame, detections):
        """Publish a JPEG preview for the dashboard without exposing the camera."""
        if self.mqtt_client is None:
            return
        now = time.monotonic()
        if now - self._last_frame_publish < self.frame_publish_interval:
            return

        preview = frame.copy()
        for detection in detections:
            x, y, width, height = detection["bbox"]
            cv2.rectangle(preview, (x, y), (x + width, y + height), (40, 220, 110), 2)
            label = f'{detection["name"]} {detection["confidence"]:.0%}'
            cv2.putText(preview, label, (x, max(20, y - 7)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (40, 220, 110), 2)

        ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            return
        self.mqtt_client.publish(self.config["mqtt"]["topic_frame"], {
            "image_b64": base64.b64encode(encoded).decode("ascii"),
            "timestamp": time.time(),
            "detections": detections,
        })
        self._last_frame_publish = now

    def publish_fps_status(self, motion_score):
        if self.mqtt_client is None:
            return

        profile_cfg = self.profiles[self.current_profile]

        payload = {
            "service": "vision_service",
            "profile": self.current_profile,
            "motion_score": round(motion_score, 2),
            "resolution": profile_cfg["resolution"],
            "framerate": profile_cfg["framerate"],
            "timestamp": time.time()
        }

        self.mqtt_client.publish(self.topic_fps_status, payload)

    def process_frame(self, frame=None):
        """Main entry point: capture frame, run Hailo inference, update buffer."""
        base_res = self.config["camera"]["base_resolution"]
        w, h = base_res

        if frame is None:
            frame = self._capture_frame(w, h)
            if frame is None:
                return []

        motion_score = self.estimate_motion(frame)

        # KAN: run inference first so we can compute semantic density from detections
        detections = self._infer(frame)

        now = time.time()
        if self.enabled and now - self.last_check_time >= self.check_interval:
            self.current_profile = self.select_profile(detections, w, h)   # KAN decision
            self.publish_fps_status(motion_score)
            self.last_check_time = now

        self.shared_buffer.update(detections, frame=frame)
        self._publish_annotated_frame(frame, detections)
        if self.mqtt_client is not None:
            self.mqtt_client.publish(self.config["mqtt"]["topic_detections"], {
                "detections": detections,
                "motion_profile": self.current_profile,
                "motion_score": round(motion_score, 2),
                "timestamp": time.time(),
            })

        return detections
