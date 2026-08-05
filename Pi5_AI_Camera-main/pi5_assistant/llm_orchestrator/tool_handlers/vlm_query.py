"""Handler for the vlm_query tool.

Sends a prompt to the Vision Service which runs the VLM (Path A or Path B)
on the current camera frame and returns a natural language description.
"""

import json
import threading


class VLMQueryHandler:
    """Dispatches VLM scene understanding requests."""

    def __init__(self, mqtt):
        self.mqtt = mqtt
        self._response_event = threading.Event()
        self._response_data = None

    def handle(self, arguments: dict, session_id: str) -> str:
        prompt = arguments.get("prompt", "Describe the scene.")
        self._response_event.clear()
        self._response_data = None

        self.mqtt.client.subscribe("vision/result")
        self.mqtt.client.message_callback_add("vision/result", self._on_result)

        self.mqtt.publish("vision/query", {
            "prompt": prompt,
            "session_id": session_id,
        })

        if self._response_event.wait(timeout=30):
            return json.dumps(self._response_data)
        return json.dumps({"error": "VLM query timed out"})

    def _on_result(self, _client, _userdata, msg):
        self._response_data = json.loads(msg.payload.decode())
        self._response_event.set()
