"""Handler for the gpio_write tool.

Publishes GPIO pin commands to the GPIO Service via MQTT.
"""

import json


class GPIOWriteHandler:
    """Sends GPIO pin state commands to the GPIO Service."""

    def __init__(self, mqtt, allowed_pins, command_topic="gpio/command"):
        self.mqtt = mqtt
        self.allowed_pins = {int(pin) for pin in allowed_pins}
        self.command_topic = command_topic

    def handle(self, arguments: dict, session_id: str) -> str:
        pin = arguments.get("pin")
        value = arguments.get("value")
        if isinstance(pin, bool) or not isinstance(pin, int) or pin not in self.allowed_pins:
            return json.dumps({"error": "GPIO pin is not approved for LLM control"})
        if not isinstance(value, bool):
            return json.dumps({"error": "GPIO value must be true or false"})

        self.mqtt.publish(self.command_topic, {
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
