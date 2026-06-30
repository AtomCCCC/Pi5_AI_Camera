"""GPIO Service — main entry point.

Subscribes to gpio/command and dispatches:
- servo_write → ServoController
- gpio_write  → PinConfig direct write
- screen      → ScreenDriver

Runs in a background thread so MQTT loop stays responsive.
"""

import sys
import os
import json
import yaml
import logging
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pi5_assistant.mqtt_client import MQTTClient
from gpio_service.servo_controller import ServoController
from gpio_service.pin_config import PinConfig
from gpio_service.screen_driver import ScreenDriver

logger = logging.getLogger(__name__)


class GPIOService:
    """Controls GPIO pins, servos, and display."""

    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

        self.mqtt = MQTTClient("gpio", self.cfg["mqtt"]["broker"],
                                self.cfg["mqtt"]["port"])
        self.pins = PinConfig()
        self.servo = ServoController(self.cfg)
        self.screen = ScreenDriver(self.cfg)

    def run(self):
        self.mqtt.subscribe(self.cfg["mqtt"]["topic_command"], self._on_command)
        print("[GPIO] Service started. Listening for commands...")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            self.stop()

    def _on_command(self, payload):
        cmd_type = payload.get("type")
        session_id = payload.get("session_id", "unknown")

        logger.info(f"[GPIO] [{session_id}] type={cmd_type}")

        if cmd_type == "servo":
            servo = payload["servo"]
            angle = payload["angle"]
            threading.Thread(
                target=self.servo.set_angle,
                args=(servo, angle),
                daemon=True,
            ).start()

        elif cmd_type == "gpio":
            pin = payload["pin"]
            value = payload["value"]
            self.pins.write_pin(pin, value)

        elif cmd_type == "screen":
            content = payload.get("content", "")
            clear = payload.get("clear", True)
            threading.Thread(
                target=self.screen.display,
                args=(content, clear),
                daemon=True,
            ).start()

        else:
            logger.warning(f"Unknown command type: {cmd_type}")

    def stop(self):
        self.servo.cleanup()
        self.pins.stop()
        self.screen.cleanup()
        self.mqtt.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    svc = GPIOService()
    svc.run()
