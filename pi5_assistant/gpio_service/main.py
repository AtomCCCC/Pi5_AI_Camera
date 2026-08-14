"""GPIO Service — main entry point.

Subscribes to gpio/command and dispatches:
- servo_write → ServoController
- gpio_write  → PinConfig direct write
- screen      → ScreenDriver

Servo writes stay on MQTT's ordered callback thread; slow screen rendering uses
a background thread so it cannot delay gimbal commands.
"""

import sys
import os
import json
import yaml
import logging
import math
import signal
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pi5_assistant.mqtt_client import MQTTClient
from gpio_service.servo_controller import ServoController
from gpio_service.pin_config import PinConfig
from gpio_service.screen_driver import ScreenDriver

logger = logging.getLogger(__name__)


class GPIOService:
    """Controls GPIO pins, servos, and display."""

    def __init__(self, config_path: str | None = None):
        config_path = config_path or Path(__file__).with_name("config.yaml")
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

        self.mqtt = MQTTClient("gpio", self.cfg["mqtt"]["broker"],
                                self.cfg["mqtt"]["port"])
        self.control_topic = self.cfg["mqtt"].get(
            "topic_control_command", "control/command"
        )
        self.manual_override_seconds = float(
            self.cfg.get("arbitration", {}).get(
                "manual_override_seconds", 3.0
            )
        )
        if (
            not math.isfinite(self.manual_override_seconds)
            or self.manual_override_seconds < 0
        ):
            raise ValueError(
                "arbitration.manual_override_seconds cannot be negative"
            )
        self._manual_override_until = 0.0
        self.pins = PinConfig()
        self.servo = ServoController(self.cfg)
        self.screen = ScreenDriver(self.cfg)
        self._stop_event = threading.Event()
        self._stopped = False

    def run(self):
        self.mqtt.subscribe(self.cfg["mqtt"]["topic_command"], self._on_command)
        print("[GPIO] Service started. Listening for commands...")
        try:
            self._stop_event.wait()
        except KeyboardInterrupt:
            self.request_stop()
        finally:
            self.stop()

    def _on_command(self, payload):
        cmd_type = payload.get("type")
        session_id = payload.get("session_id", "unknown")

        if cmd_type == "gimbal" and payload.get("source") == "visual_pid":
            logger.debug("[GPIO] [%s] type=%s", session_id, cmd_type)
        else:
            logger.info("[GPIO] [%s] type=%s", session_id, cmd_type)

        if cmd_type == "servo":
            servo = payload["servo"]
            angle = payload["angle"]
            # sysfs writes are short.  Serial execution preserves MQTT order;
            # spawning one thread per PID sample can apply stale angles late.
            applied_angle = self.servo.set_angle(servo, angle)
            if payload.get("source") != "visual_pid":
                self._manual_override_until = (
                    time.monotonic() + self.manual_override_seconds
                )
                self.mqtt.publish(self.control_topic, {
                    "type": "manual_override",
                    "duration": self.manual_override_seconds,
                    "servo": servo,
                    "angle": applied_angle,
                    "session_id": session_id,
                })

        elif cmd_type == "gimbal":
            if (
                payload.get("source") == "visual_pid"
                and time.monotonic() < self._manual_override_until
            ):
                logger.debug("ignoring visual PID during manual override")
                return
            self.servo.set_angles(payload.get("angles"))

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

    def request_stop(self):
        self._stop_event.set()

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self.servo.cleanup()
        self.pins.stop()
        self.screen.cleanup()
        self.mqtt.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    svc = GPIOService()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: svc.request_stop())
    signal.signal(signal.SIGINT, lambda _signum, _frame: svc.request_stop())
    svc.run()
