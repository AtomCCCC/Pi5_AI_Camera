"""Handler for the screen_display tool.

Publishes screen display commands to the GPIO Service via MQTT.
"""

import json


class ScreenDisplayHandler:
    """Sends display commands to the GPIO Service."""

    def __init__(self, mqtt, command_topic="gpio/command"):
        self.mqtt = mqtt
        self.command_topic = command_topic

    def handle(self, arguments: dict, session_id: str) -> str:
        content = arguments.get("content")
        if not isinstance(content, str):
            return json.dumps({"error": "content must be text"})
        clear = arguments.get("clear", True)
        if not isinstance(clear, bool):
            return json.dumps({"error": "clear must be true or false"})

        self.mqtt.publish(self.command_topic, {
            "type": "screen",
            "content": content,
            "clear": clear,
            "session_id": session_id,
        })

        return json.dumps({
            "status": "ok",
            "content_length": len(content),
        })
