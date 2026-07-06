"""Continuous real-time object detection on Hailo-10H.

Camera Module 3 → Hailo-10H YOLO pipeline → shared_buffer.

Camera capture uses rpicam-jpeg (subprocess) since OpenCV cv2.VideoCapture
does not work with Pi Camera Module 3's libcamera backend.
"""

import time
import cv2
import json
import threading
import subprocess as sp
import numpy as np


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

        self.topic_fps_status = config["mqtt"].get(
            "topic_fps_status",
            "vision/fps/status"
        )

        self._thread = None
        self._stop_event = threading.Event()

    def start(self):
        """Start continuous detection in a background thread."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="detection-pipeline", daemon=True)
        self._thread.start()
        print("[Vision] Detection pipeline started (continuous thread).")

    def stop(self):
        """Stop the detection thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._vdevice is not None:
            self._vdevice.release()
            self._vdevice = None
        print("[Vision] Detection pipeline stopped.")

    def _run_loop(self):
        """Continuous loop: capture → infer → update buffer."""
        base_res = self.config["camera"]["base_resolution"]
        w, h = base_res

        while not self._stop_event.is_set():
            frame = self._capture_frame(w, h)
            if frame is None:
                time.sleep(0.01)
                continue

            motion_score = self.estimate_motion(frame)

            now = time.time()
            if self.enabled and now - self.last_check_time >= self.check_interval:
                self.current_profile = self.select_profile(motion_score)
                self.publish_fps_status(motion_score)
                self.last_check_time = now

            detections = self._infer(frame)

            self.shared_buffer.update(
                {
                    "detections": detections,
                    "motion_profile": self.current_profile,
                    "motion_score": round(motion_score, 2),
                },
                frame=frame,
            )

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
        print("[Vision] Hailo-10H YOLO model initialized.")

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

    def select_profile(self, motion_score):
        if motion_score > self.motion_threshold:
            return "high_motion"
        return "low_motion"

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

        self.mqtt_client.publish(self.topic_fps_status, json.dumps(payload))

    def process_frame(self, frame=None):
        """Single-frame entry point (used for testing / manual control)."""
        base_res = self.config["camera"]["base_resolution"]
        w, h = base_res

        if frame is None:
            frame = self._capture_frame(w, h)
            if frame is None:
                return []

        motion_score = self.estimate_motion(frame)

        now = time.time()
        if self.enabled and now - self.last_check_time >= self.check_interval:
            self.current_profile = self.select_profile(motion_score)
            self.publish_fps_status(motion_score)
            self.last_check_time = now

        detections = self._infer(frame)

        self.shared_buffer.update(
            {
                "detections": detections,
                "motion_profile": self.current_profile,
                "motion_score": round(motion_score, 2),
            },
            frame=frame,
        )

        return detections
