"""Vision Service — main entry point.

Starts the continuous detection pipeline on Hailo-10H.
Subscribes to vision/query for on-demand VLM analysis.
Publishes results to vision/result.
"""

import sys
import os
import yaml
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pi5_assistant.mqtt_client import MQTTClient
from vision_service.shared_buffer import SharedDetectionBuffer
from vision_service.detection_pipeline import DetectionPipeline
from vision_service.vlm_engine import VLMEngine


class VisionService:
    """Orchestrates continuous detection + on-demand VLM."""

    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

        self.mqtt = MQTTClient("vision", self.cfg["mqtt"]["broker"],
                               self.cfg["mqtt"]["port"])
        self.buffer = SharedDetectionBuffer(
            max_age_ms=self.cfg["shared_buffer"]["max_age_ms"]
        )
        self.pipeline = DetectionPipeline(self.buffer, self.cfg)
        self.vlm = VLMEngine(self.cfg["vlm"]["mode"], self.cfg)

    def run(self):
        self.mqtt.subscribe(self.cfg["mqtt"]["topic_query"], self._on_query)
        self.pipeline.start()

        print("[Vision] Service started. Detection pipeline running.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            self.stop()

    def _on_query(self, payload):
        """Handle a VLM query request."""
        prompt = payload.get("prompt", "Describe what you see in this image.")
        session_id = payload.get("session_id", "unknown")

        frame = self.buffer.get_frame()
        if frame is None:
            self.mqtt.publish(self.cfg["mqtt"]["topic_result"], {
                "error": "No frame available yet",
                "session_id": session_id,
            })
            return

        print(f"[Vision] VLM query: {prompt[:60]}...")
        description = self.vlm.query(frame, prompt)

        self.mqtt.publish(self.cfg["mqtt"]["topic_result"], {
            "description": description,
            "session_id": session_id,
        })

    def stop(self):
        self.pipeline.stop()
        self.mqtt.stop()


if __name__ == "__main__":
    service = VisionService()
    service.run()
