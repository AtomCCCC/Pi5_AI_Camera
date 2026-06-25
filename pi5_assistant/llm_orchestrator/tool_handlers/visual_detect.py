"""Handler for the visual_detect tool.

Reads the latest detection results from the Vision Service's shared buffer
via MQTT. This is a request/response pattern — publishes a query and waits
for a response on vision/result.
"""

import json
import threading
import time


class VisualDetectHandler:
    """Dispatches visual detection requests to the Vision Service."""

    def __init__(self, mqtt):
        self.mqtt = mqtt
        self._response_event = threading.Event()
        self._response_data = None

    def handle(self, arguments: dict, session_id: str) -> str:
        """Request current detections and return structured results."""
        self._response_event.clear()
        self._response_data = None

        # Subscribe for one response
        self.mqtt.client.subscribe("vision/detect_result")
        self.mqtt.client.message_callback_add("vision/detect_result",
                                                self._on_result)

        self.mqtt.publish("vision/detect", {
            "classes": arguments.get("classes", []),
            "min_confidence": arguments.get("min_confidence", 0.5),
            "session_id": session_id,
        })

        # Wait for response
        if self._response_event.wait(timeout=5):
            return json.dumps(self._response_data)
        return json.dumps({"error": "Vision service timed out"})

    def _on_result(self, _client, _userdata, msg):
        self._response_data = json.loads(msg.payload.decode())
        self._response_event.set()
