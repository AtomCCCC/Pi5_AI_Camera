import time
import cv2
import json


class DetectionPipeline:
    def __init__(self, config, shared_buffer, mqtt_client=None):
        self.config = config
        self.shared_buffer = shared_buffer
        self.mqtt_client = mqtt_client

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

    def process_frame(self, frame):
        motion_score = self.estimate_motion(frame)

        now = time.time()
        if self.enabled and now - self.last_check_time >= self.check_interval:
            self.current_profile = self.select_profile(motion_score)
            self.publish_fps_status(motion_score)
            self.last_check_time = now

        # TODO: Add Hailo YOLO inference here.
        # For now, detections is empty so the pipeline structure works.
        detections = []

        self.shared_buffer.update(
            {
                "detections": detections,
                "motion_profile": self.current_profile,
                "motion_score": round(motion_score, 2)
            },
            frame=frame
        )

        return detections