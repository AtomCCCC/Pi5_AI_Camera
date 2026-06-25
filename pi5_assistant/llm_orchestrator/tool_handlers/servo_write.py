"""Handler for the servo_write tool.

Publishes servo commands to the GPIO Service via MQTT.
"""

import json


class ServoWriteHandler:
    """Sends servo angle commands to the GPIO Service."""

    def __init__(self, mqtt):
        self.mqtt = mqtt

    def handle(self, arguments: dict, session_id: str) -> str:
        servo = arguments["servo"]
        angle = arguments["angle"]

        self.mqtt.publish("gpio/command", {
            "type": "servo",
            "servo": servo,
            "angle": angle,
            "session_id": session_id,
        })

        return json.dumps({
            "status": "ok",
            "servo": servo,
            "angle": angle,
        })
