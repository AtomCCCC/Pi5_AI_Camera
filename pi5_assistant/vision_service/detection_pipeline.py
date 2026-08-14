"""Continuous real-time object detection on Hailo-10H.

Camera Module 3 → Hailo-10H YOLO pipeline → shared_buffer.

Camera capture uses rpicam-jpeg (subprocess) since OpenCV cv2.VideoCapture
does not work with Pi Camera Module 3's libcamera backend.
"""

import time
import cv2
import math
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
_AUTO_TRACKING_TARGET = object()


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
        detection_cfg = config["detection"]

        self.enabled = dynamic_cfg.get("enabled", True)
        self.check_interval = dynamic_cfg.get("check_interval", 0.5)
        self.motion_threshold = dynamic_cfg.get("motion_threshold", 30)
        self.profiles = dynamic_cfg.get("profiles", {})

        self.current_profile = "low_motion"
        self.last_check_time = 0
        self.previous_gray = None
        self._running = False
        self._thread = None
        self.hef_path = Path(detection_cfg.get(
            "hef_path",
            "/usr/local/hailo/resources/models/hailo10h/"
            "hailo_yolov8n_4_classes_vga.hef",
        )).expanduser()
        self.class_names = detection_cfg.get(
            "labels",
            ["person", "bicycle", "car", "motorcycle"],
        )
        if not isinstance(self.class_names, list) or not self.class_names:
            raise ValueError("detection.labels must be a non-empty list")
        self.input_color_order = str(
            detection_cfg.get("input_color_order", "rgb")
        ).lower()
        if self.input_color_order not in {"rgb", "bgr"}:
            raise ValueError("detection.input_color_order must be 'rgb' or 'bgr'")
        self._last_frame_publish = 0.0
        self.frame_publish_interval = config["mqtt"].get("frame_publish_interval", 0.2)

        roi_cfg = config.get("roi", {})
        self.roi_enabled = bool(roi_cfg.get("enabled", False))
        self.roi_max_regions = int(roi_cfg.get("max_regions", 1))
        self.roi_padding_ratio = float(roi_cfg.get("padding_ratio", 0.05))
        raw_fixed_size = roi_cfg.get("fixed_size")
        if raw_fixed_size is None:
            self.roi_fixed_size = None
        elif isinstance(raw_fixed_size, (list, tuple)) and len(raw_fixed_size) == 2:
            try:
                self.roi_fixed_size = tuple(int(value) for value in raw_fixed_size)
            except (TypeError, ValueError) as exc:
                raise ValueError("roi.fixed_size values must be integers") from exc
        else:
            raise ValueError("roi.fixed_size must be [width, height]")
        self.roi_max_dimension = int(roi_cfg.get("max_dimension", 320))
        self.roi_jpeg_quality = int(roi_cfg.get("jpeg_quality", 80))
        self.roi_min_confidence = float(
            roi_cfg.get("minimum_confidence", detection_cfg["confidence"])
        )
        raw_target_labels = roi_cfg.get("target_labels", [])
        if not isinstance(raw_target_labels, (list, tuple)):
            raise ValueError("roi.target_labels must be a list")
        self.roi_target_labels = tuple(
            str(label) for label in raw_target_labels
        )
        self.target_lock_max_distance = float(
            roi_cfg.get("target_lock_max_distance", 0.25)
        )
        self.target_reacquire_frames = int(
            roi_cfg.get("target_reacquire_frames", 3)
        )
        self._locked_target = None
        self._locked_target_misses = 0
        self.roi_publish_interval = float(
            roi_cfg.get("publish_interval", self.frame_publish_interval)
        )
        self.topic_roi = config["mqtt"].get("topic_roi", "vision/roi")
        self._last_roi_publish = 0.0
        self._frame_sequence = 0
        self._validate_roi_config()

        self.topic_fps_status = config["mqtt"].get(
            "topic_fps_status",
            "vision/fps/status"
        )

    def _validate_roi_config(self):
        if self.roi_max_regions < 1:
            raise ValueError("roi.max_regions must be at least 1")
        if not 0 <= self.roi_padding_ratio <= 1:
            raise ValueError("roi.padding_ratio must be between 0 and 1")
        if self.roi_fixed_size is not None:
            fixed_width, fixed_height = self.roi_fixed_size
            if fixed_width < 1 or fixed_height < 1:
                raise ValueError("roi.fixed_size dimensions must be positive")
            base_width, base_height = self.config["camera"]["base_resolution"]
            if fixed_width > base_width or fixed_height > base_height:
                raise ValueError(
                    "roi.fixed_size cannot exceed camera.base_resolution"
                )
        if self.roi_max_dimension < 1:
            raise ValueError("roi.max_dimension must be at least 1")
        if (
            self.roi_fixed_size is not None
            and self.roi_max_dimension < max(self.roi_fixed_size)
        ):
            raise ValueError(
                "roi.max_dimension must be at least the largest fixed_size dimension"
            )
        if not 1 <= self.roi_jpeg_quality <= 100:
            raise ValueError("roi.jpeg_quality must be between 1 and 100")
        if not math.isfinite(self.roi_min_confidence) or not (
            0.0 <= self.roi_min_confidence <= 1.0
        ):
            raise ValueError("roi.minimum_confidence must be between 0 and 1")
        if self.roi_publish_interval < 0:
            raise ValueError("roi.publish_interval cannot be negative")
        if not 0 < self.target_lock_max_distance <= 1:
            raise ValueError(
                "roi.target_lock_max_distance must be in the range (0, 1]"
            )
        if self.target_reacquire_frames < 0:
            raise ValueError("roi.target_reacquire_frames cannot be negative")
        # KAN: load the adaptive controller (pure numpy, no torch)
        kan_weights = Path(__file__).with_name("kan_weights.npz")
        self.kan = load_kan(str(kan_weights))
        self.last_s_id = 0.0
        self.last_alpha = 0.0
        self.target_classes = []

    def set_target_classes(self, classes):
        self.target_classes = list(classes)

    def _init_hailo(self):
        """Initialize the configured Hailo-10H model once."""
        if self._configured_model is not None:
            return

        from hailo_platform import VDevice, HailoSchedulingAlgorithm

        if not self.hef_path.is_file():
            raise FileNotFoundError(
                f"Hailo HEF not found: {self.hef_path}. "
                "Set detection.hef_path in vision_service/config.yaml."
            )

        params = VDevice.create_params()
        params.scheduling_algorithm = HailoSchedulingAlgorithm.ROUND_ROBIN
        params.group_id = "SHARED"
        self._vdevice = VDevice(params)

        infer_model = self._vdevice.create_infer_model(str(self.hef_path))
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
        if self.target_classes:
            detections = [d for d in detections
                          if d.get("name") in self.target_classes]
        s_id = compute_s_id(detections, frame_w, frame_h)
        delta_s_id = abs(s_id - self.last_s_id)
        self.last_s_id = s_id

        if self.kan is None:
            return self.current_profile

        alpha = kan_infer(self.kan, s_id, delta_s_id)
        self.last_alpha = alpha

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

        model_frame = cv2.resize(frame, (self._model_w, self._model_h))
        if self.input_color_order == "rgb":
            model_frame = cv2.cvtColor(model_frame, cv2.COLOR_BGR2RGB)
        input_batch = np.expand_dims(model_frame.astype(np.uint8), 0)

        out = {name: np.empty(buf.shape, dtype=buf.dtype) for name, buf in self._output_buffers.items()}
        b = self._configured_model.create_bindings(output_buffers=out)
        b.input().set_buffer(np.array(input_batch))
        self._configured_model.run([b], timeout=10000)

        raw = out[list(out.keys())[0]]
        return self._postprocess(raw, frame.shape)

    def _postprocess(self, raw: np.ndarray, frame_shape) -> list[dict]:
        """Parse normalized HAILO_NMS_BY_CLASS output into pixel ``xywh`` boxes."""
        conf_threshold = self.config["detection"]["confidence"]
        frame_height, frame_width = frame_shape[:2]

        data = raw.ravel()
        idx = 0
        detections = []
        for class_id, class_name in enumerate(self.class_names):
            if idx >= len(data):
                logger.warning("Truncated Hailo NMS output at class %s", class_id)
                break
            count = int(data[idx])
            idx += 1
            if count <= 0:
                continue
            available = (len(data) - idx) // 5
            if count > available:
                logger.warning(
                    "Hailo NMS class %s reports %s boxes but only %s are available",
                    class_id, count, available
                )
                count = available
            bboxes = data[idx:idx + count * 5].reshape(-1, 5)
            idx += count * 5
            for y_min, x_min, y_max, x_max, conf in bboxes:
                if conf >= conf_threshold:
                    x1 = max(0, min(frame_width, round(float(x_min) * frame_width)))
                    y1 = max(0, min(frame_height, round(float(y_min) * frame_height)))
                    x2 = max(0, min(frame_width, round(float(x_max) * frame_width)))
                    y2 = max(0, min(frame_height, round(float(y_max) * frame_height)))
                    width = x2 - x1
                    height = y2 - y1
                    if width <= 0 or height <= 0:
                        continue
                    detections.append({
                        "class": class_id,
                        "name": class_name,
                        "confidence": round(float(conf), 3),
                        "bbox": [x1, y1, width, height],
                    })
        return detections

    def start(self):
        """Start continuous capture and inference in a background thread."""
        if self._running:
            return
        # Fail synchronously on a missing/incompatible HEF instead of leaving a
        # background loop that only logs one retry per second.
        self._init_hailo()
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

    def _publish_annotated_frame(
        self, frame, detections, timestamp, tracking_target=None
    ):
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
            label = f'DET {detection["name"]} {detection["confidence"]:.0%}'
            cv2.putText(preview, label, (x, max(20, y - 7)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (40, 220, 110), 2)

        # A detector bbox naturally changes with target distance. Draw the
        # actual fixed crop separately so it cannot be mistaken for that box.
        if tracking_target is not None:
            frame_height, frame_width = frame.shape[:2]
            crop_bbox = self._crop_bounds(
                tracking_target.get("bbox"), frame_width, frame_height
            )
            if crop_bbox is not None:
                roi_x, roi_y, roi_width, roi_height = crop_bbox
                roi_colour = (40, 160, 255)
                cv2.rectangle(
                    preview,
                    (roi_x, roi_y),
                    (roi_x + roi_width, roi_y + roi_height),
                    roi_colour,
                    3,
                )
                cv2.putText(
                    preview,
                    f"ROI {roi_width}x{roi_height}",
                    (roi_x, min(frame_height - 8, roi_y + 22)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    roi_colour,
                    2,
                )

        ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            return
        self.mqtt_client.publish(self.config["mqtt"]["topic_frame"], {
            "image_b64": base64.b64encode(encoded.tobytes()).decode("ascii"),
            "timestamp": timestamp,
            "detections": detections,
        })
        self._last_frame_publish = now

    def _crop_bounds(self, bbox, frame_width, frame_height):
        """Return a complete-frame ``[x, y, width, height]`` ROI crop.

        With ``roi.fixed_size`` configured, the crop is centred on the raw
        detection centre and shifted inward at frame edges so its dimensions
        remain fixed. The detection centre itself remains the PID coordinate.
        """
        try:
            x, y, width, height = (float(value) for value in bbox)
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(value) for value in (x, y, width, height)):
            return None
        if width <= 0 or height <= 0:
            return None
        if x + width <= 0 or y + height <= 0 or x >= frame_width or y >= frame_height:
            return None

        if self.roi_fixed_size is not None:
            crop_width, crop_height = self.roi_fixed_size
            # Fixed means exact. Do not silently publish a smaller ROI when an
            # unexpected camera frame violates the configured size contract.
            if frame_width < crop_width or frame_height < crop_height:
                return None
            center_x = x + width / 2.0
            center_y = y + height / 2.0
            x1 = round(center_x - crop_width / 2.0)
            y1 = round(center_y - crop_height / 2.0)
            x1 = max(0, min(int(frame_width) - crop_width, x1))
            y1 = max(0, min(int(frame_height) - crop_height, y1))
            return [x1, y1, crop_width, crop_height]

        pad_x = width * self.roi_padding_ratio
        pad_y = height * self.roi_padding_ratio
        x1 = max(0, math.floor(x - pad_x))
        y1 = max(0, math.floor(y - pad_y))
        x2 = min(frame_width, math.ceil(x + width + pad_x))
        y2 = min(frame_height, math.ceil(y + height + pad_y))
        if x2 <= x1 or y2 <= y1:
            return None
        return [x1, y1, x2 - x1, y2 - y1]

    def _encode_roi(self, frame, detection, index):
        frame_height, frame_width = frame.shape[:2]
        crop_bbox = self._crop_bounds(
            detection.get("bbox"), frame_width, frame_height
        )
        if crop_bbox is None:
            return None

        x, y, width, height = crop_bbox
        roi_frame = frame[y:y + height, x:x + width]
        if roi_frame.size == 0:
            return None

        roi_height, roi_width = roi_frame.shape[:2]
        largest_dimension = max(roi_width, roi_height)
        if self.roi_fixed_size is not None:
            if (roi_width, roi_height) != self.roi_fixed_size:
                return None
        elif largest_dimension > self.roi_max_dimension:
            scale = self.roi_max_dimension / largest_dimension
            output_width = max(1, round(roi_width * scale))
            output_height = max(1, round(roi_height * scale))
            roi_frame = cv2.resize(
                roi_frame, (output_width, output_height),
                interpolation=cv2.INTER_AREA
            )

        ok, encoded = cv2.imencode(
            ".jpg", roi_frame,
            [cv2.IMWRITE_JPEG_QUALITY, self.roi_jpeg_quality]
        )
        if not ok:
            return None

        encoded_height, encoded_width = roi_frame.shape[:2]
        return {
            "index": index,
            "class": detection.get("class"),
            "name": detection.get("name", "unknown"),
            "confidence": float(detection.get("confidence", 0.0)),
            "bbox": list(detection["bbox"]),
            # This is the target coordinate used by the PID loop.  It is the
            # centre of the original detection in the complete camera frame,
            # not the centre of the padded/clipped JPEG crop.
            "roi_center": list(self._pixel_center(detection)),
            "crop_bbox": crop_bbox,
            "roi_size": {
                "width": width,
                "height": height,
            },
            "fixed_size": self.roi_fixed_size is not None,
            "image_size": {
                "width": encoded_width,
                "height": encoded_height,
            },
            "mime_type": "image/jpeg",
            "image_b64": base64.b64encode(encoded.tobytes()).decode("ascii"),
        }

    def _rank_roi_candidates(
        self, detections, frame_width=None, frame_height=None
    ):
        """Return the detections eligible to become the tracked ROI."""
        candidates = []
        for detection in detections:
            try:
                confidence = float(detection.get("confidence", 0.0))
            except (AttributeError, TypeError, ValueError):
                continue
            if not math.isfinite(confidence):
                continue
            if (
                frame_width is not None
                and frame_height is not None
                and self._crop_bounds(
                    detection.get("bbox"), frame_width, frame_height
                ) is None
            ):
                continue
            if confidence >= self.roi_min_confidence and (
                not self.roi_target_labels
                or str(detection.get("name", "")) in self.roi_target_labels
            ):
                candidates.append(detection)
        return sorted(
            candidates,
            key=lambda detection: float(detection.get("confidence", 0.0)),
            reverse=True,
        )

    @staticmethod
    def _pixel_center(detection):
        """Return the ROI centre in complete-frame pixel coordinates."""
        x, y, width, height = map(float, detection["bbox"][:4])
        return x + width / 2.0, y + height / 2.0

    @classmethod
    def _normalised_center(cls, detection, frame_width, frame_height):
        center_x, center_y = cls._pixel_center(detection)
        return (
            center_x / frame_width,
            center_y / frame_height,
        )

    def _select_tracking_target(self, detections, frame_width, frame_height):
        """Keep the same person/face when confidence ordering fluctuates."""
        candidates = self._rank_roi_candidates(
            detections, frame_width, frame_height
        )
        if not candidates:
            self._locked_target_misses += 1
            if self._locked_target_misses > self.target_reacquire_frames:
                self._locked_target = None
            return None

        chosen = None
        if self._locked_target is not None:
            previous_label, previous_x, previous_y = self._locked_target
            nearest_distance = float("inf")
            for candidate in candidates:
                if str(candidate.get("name", "")) != previous_label:
                    continue
                center_x, center_y = self._normalised_center(
                    candidate, frame_width, frame_height
                )
                distance = math.hypot(
                    center_x - previous_x, center_y - previous_y
                )
                if distance < nearest_distance:
                    nearest_distance = distance
                    chosen = candidate
            if nearest_distance > self.target_lock_max_distance:
                chosen = None

        if chosen is None and self._locked_target is not None:
            self._locked_target_misses += 1
            if self._locked_target_misses <= self.target_reacquire_frames:
                return None

        if chosen is None:
            chosen = candidates[0]

        center_x, center_y = self._normalised_center(
            chosen, frame_width, frame_height
        )
        self._locked_target = (
            str(chosen.get("name", "")), center_x, center_y
        )
        self._locked_target_misses = 0
        return chosen

    def _publish_roi_images(
        self,
        frame,
        detections,
        timestamp,
        tracking_target=_AUTO_TRACKING_TARGET,
    ):
        """Publish confidence-ranked object crops as one atomic MQTT message."""
        if not self.roi_enabled or self.mqtt_client is None:
            return

        now = time.monotonic()
        if now - self._last_roi_publish < self.roi_publish_interval:
            return

        frame_height, frame_width = frame.shape[:2]
        ranked_candidates = self._rank_roi_candidates(
            detections, frame_width, frame_height
        )
        if tracking_target is _AUTO_TRACKING_TARGET:
            ranked = ranked_candidates
        elif tracking_target is None:
            ranked = []
        else:
            ranked = [tracking_target] + [
                candidate for candidate in ranked_candidates
                if candidate is not tracking_target
            ]
        rois = []
        for detection in ranked:
            roi = self._encode_roi(frame, detection, len(rois))
            if roi is not None:
                rois.append(roi)
            if len(rois) >= self.roi_max_regions:
                break

        self.mqtt_client.publish(self.topic_roi, {
            "timestamp": timestamp,
            "frame_size": {
                "width": frame_width,
                "height": frame_height,
            },
            "frame_center": [frame_width / 2.0, frame_height / 2.0],
            "coordinate_space": "pixels",
            "rois": rois,
        })
        self._last_roi_publish = now

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

        frame_timestamp = time.time()
        self._frame_sequence += 1
        frame_height, frame_width = frame.shape[:2]
        tracking_target = self._select_tracking_target(
            detections, frame_width, frame_height
        )
        tracking_target_payload = None
        if tracking_target is not None:
            tracking_target_payload = dict(tracking_target)
            tracking_target_payload["roi_center"] = list(
                self._pixel_center(tracking_target)
            )
        self.shared_buffer.update(detections, frame=frame)
        self._publish_annotated_frame(
            frame, detections, frame_timestamp, tracking_target
        )
        self._publish_roi_images(
            frame, detections, frame_timestamp, tracking_target
        )
        if self.mqtt_client is not None:
            self.mqtt_client.publish(self.config["mqtt"]["topic_detections"], {
                "detections": detections,
                # The PID service uses the same target selection as the ROI
                # publisher, while retaining the complete list for observers.
                "tracking_target": tracking_target_payload,
                "frame_size": {
                    "width": frame_width,
                    "height": frame_height,
                },
                # At the configured 640x640 capture size this is [320, 320].
                "frame_center": [frame_width / 2.0, frame_height / 2.0],
                "coordinate_space": "pixels",
                "sequence": self._frame_sequence,
                "motion_profile": self.current_profile,
                "motion_score": round(motion_score, 2),
                "timestamp": frame_timestamp,
            })

        return detections
