"""Handler for the servo_write tool.

Publishes servo commands to the GPIO Service via MQTT.
"""

import json


class ServoWriteHandler:
    """Sends servo angle commands to the GPIO Service."""

    def __init__(self, mqtt, command_topic="gpio/command"):
        self.mqtt = mqtt
        self.command_topic = command_topic

    def handle(self, arguments: dict, session_id: str) -> str:
        servo = arguments.get("servo")
        angle = arguments.get("angle")
        if isinstance(servo, bool) or not isinstance(servo, int) or servo not in (1, 2):
            return json.dumps({"error": "servo must be 1 or 2"})
        if isinstance(angle, bool) or not isinstance(angle, int) or not 0 <= angle <= 180:
            return json.dumps({"error": "angle must be an integer from 0 to 180"})

        self.mqtt.publish(self.command_topic, {
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
