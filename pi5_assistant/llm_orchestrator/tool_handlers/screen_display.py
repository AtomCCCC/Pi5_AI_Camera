"""Handler for the screen_display tool.

Publishes screen display commands to the GPIO Service via MQTT.
"""

import json


class ScreenDisplayHandler:
    """Sends display commands to the GPIO Service."""

    def __init__(self, mqtt):
        self.mqtt = mqtt

    def handle(self, arguments: dict, session_id: str) -> str:
        content = arguments["content"]
        clear = arguments.get("clear", True)

        self.mqtt.publish("gpio/command", {
            "type": "screen",
            "content": content,
            "clear": clear,
            "session_id": session_id,
        })

        return json.dumps({
            "status": "ok",
            "content_length": len(content),
        })
