"""Handler for the gpio_write tool.

Publishes GPIO pin commands to the GPIO Service via MQTT.
"""

import json


class GPIOWriteHandler:
    """Sends GPIO pin state commands to the GPIO Service."""

    def __init__(self, mqtt):
        self.mqtt = mqtt

    def handle(self, arguments: dict, session_id: str) -> str:
        pin = arguments["pin"]
        value = arguments["value"]

        self.mqtt.publish("gpio/command", {
            "type": "gpio",
            "pin": pin,
            "value": value,
            "session_id": session_id,
        })

        return json.dumps({
            "status": "ok",
            "pin": pin,
            "value": value,
        })
