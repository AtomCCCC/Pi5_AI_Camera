"""Handler for the visual_detect tool.

Reads the latest detection results from the Vision Service's shared buffer
via MQTT. This is a request/response pattern — publishes a query and waits
for a response on vision/result.
"""

import json
import logging
import threading

logger = logging.getLogger(__name__)


class VisualDetectHandler:
    """Dispatches visual detection requests to the Vision Service."""

    def __init__(self, mqtt, request_topic="vision/detect",
                 response_topic="vision/detect_result"):
        self.mqtt = mqtt
        self.request_topic = request_topic
        self.response_topic = response_topic
        self._response_event = threading.Event()
        self._response_data = None

    def handle(self, arguments: dict, session_id: str) -> str:
        """Request current detections and return structured results."""
        self._response_event.clear()
        self._response_data = None

        # Subscribe for one response
        self.mqtt.client.subscribe(self.response_topic)
        self.mqtt.client.message_callback_add(self.response_topic,
                                                self._on_result)

        self.mqtt.publish(self.request_topic, {
            "classes": arguments.get("classes", []),
            "min_confidence": arguments.get("min_confidence", 0.5),
            "session_id": session_id,
        })

        # Wait for response
        if self._response_event.wait(timeout=5):
            return json.dumps(self._response_data)
        return json.dumps({"error": "Vision service timed out"})

    def _on_result(self, _client, _userdata, msg):
        try:
            self._response_data = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("Ignoring invalid vision detection response")
            return
        self._response_event.set()
