"""MQTT service for closed-loop two-axis visual tracking.

The Vision service owns the camera and publishes continuous detections, the
Control service runs the two PID loops, and the GPIO service owns the FS90 PWM
outputs.

Subscribes:  vision/detections
Publishes:   gpio/command       servo angles
             control/status     mode, angles and latency
             control/power_mode active/idle transition hints

Run:  python -m control_service.main
"""

import logging
import os
from pathlib import Path
import sys
import time

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pi5_assistant.mqtt_client import MQTTClient
from control_service.control_loop import (
    ControlPolicy,
    GimbalController,
    detections_from_payload,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("control_service")


class ControlService:
    def __init__(self, config_path=None, mqtt_client=None):
        config_path = config_path or Path(__file__).with_name("config.yaml")
        with open(config_path) as config_file:
            self.cfg = yaml.safe_load(config_file)

        mqtt_cfg = self.cfg["mqtt"]
        self.t_in = mqtt_cfg.get(
            "topic_detections", mqtt_cfg.get("topic_detect_result")
        )
        if not self.t_in:
            raise ValueError("mqtt.topic_detections is required")
        self.t_gpio = mqtt_cfg["topic_gpio_command"]
        self.t_status = mqtt_cfg["topic_status"]
        self.t_power = mqtt_cfg["topic_power_mode"]

        servo_cfg = self.cfg["servo"]
        self.pan_ch = servo_cfg["pan_channel"]
        self.tilt_ch = servo_cfg["tilt_channel"]

        control_cfg = self.cfg.get("control", {})
        self.default_frame_size = (
            int(control_cfg.get("frame_width", 640)),
            int(control_cfg.get("frame_height", 640)),
        )
        self.command_interval = float(control_cfg.get("command_interval", 0.03))
        if self.command_interval < 0:
            raise ValueError("control.command_interval cannot be negative")

        self.mqtt = mqtt_client or MQTTClient(
            "control", mqtt_cfg["broker"], mqtt_cfg["port"]
        )
        self.policy = ControlPolicy(
            min_switch_interval=control_cfg.get("min_switch_interval", 0.15),
            empty_frames_to_idle=control_cfg.get("empty_frames_to_idle", 3),
            confidence_threshold=control_cfg.get("confidence_threshold", 0.65),
            target_labels=control_cfg.get("target_labels") or None,
        )
        self.gimbal = GimbalController(
            pan_config=servo_cfg.get("pan"),
            tilt_config=servo_cfg.get("tilt"),
            dt_min=control_cfg.get("dt_min", 0.005),
            dt_max=control_cfg.get("dt_max", 0.2),
        )
        self.last_time = None
        self.last_command_time = 0.0
        self.last_commanded_angles = {}
        self.last_active = None

    def run(self):
        self.mqtt.subscribe(self.t_in, self._on_detections)
        logger.info("control_service up - subscribed to %s", self.t_in)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("stopping")
        finally:
            self.mqtt.stop()

    def _on_detections(self, payload):
        now = time.perf_counter()
        dt = (1.0 / 30.0) if self.last_time is None else now - self.last_time
        self.last_time = now
        started_ns = time.perf_counter_ns()

        detections = detections_from_payload(payload, self.default_frame_size)
        settings, target, mode = self.policy.decide(detections, now)
        if target is not None:
            pan, tilt = self.gimbal.track(target, dt)
        else:
            pan, tilt = self.gimbal.hold()

        session_id = payload.get("session_id", "")
        if (
            target is not None
            and now - self.last_command_time >= self.command_interval
        ):
            self._publish_angle(self.pan_ch, pan, session_id)
            self._publish_angle(self.tilt_ch, tilt, session_id)
            self.last_command_time = now

        latency_ms = (time.perf_counter_ns() - started_ns) / 1e6
        self.mqtt.publish(self.t_status, {
            "mode": mode,
            "note": settings.note,
            "pan": pan,
            "tilt": tilt,
            "pid_active": target is not None,
            "latency_ms": round(latency_ms, 3),
            "session_id": session_id,
        })

        active = mode == "tracking"
        if active != self.last_active:
            self.last_active = active
            self.mqtt.publish(self.t_power, {"active": active})
            logger.info("power_mode -> %s", "ACTIVE" if active else "IDLE")

    def _publish_angle(self, channel, angle, session_id):
        angle = round(angle)
        if self.last_commanded_angles.get(channel) == angle:
            return
        self.last_commanded_angles[channel] = angle
        self.mqtt.publish(self.t_gpio, {
            "type": "servo",
            "servo": channel,
            "angle": angle,
            "source": "visual_pid",
            "session_id": session_id,
        })


if __name__ == "__main__":
    ControlService().run()
