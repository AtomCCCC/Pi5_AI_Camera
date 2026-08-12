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
import math
import os
from pathlib import Path
import sys
import threading
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
        self.t_command = mqtt_cfg.get("topic_command", "control/command")

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
        self.detection_timeout = float(
            control_cfg.get("detection_timeout", 0.75)
        )
        self.max_detection_age = float(
            control_cfg.get("max_detection_age", 0.5)
        )
        self.max_manual_override = float(
            control_cfg.get("max_manual_override", 30.0)
        )
        if not all(math.isfinite(value) for value in (
            self.command_interval,
            self.detection_timeout,
            self.max_detection_age,
            self.max_manual_override,
        )):
            raise ValueError("control timing values must be finite")
        if self.detection_timeout <= 0:
            raise ValueError("control.detection_timeout must be positive")
        if self.max_detection_age < 0:
            raise ValueError("control.max_detection_age cannot be negative")
        if self.max_manual_override < 0:
            raise ValueError("control.max_manual_override cannot be negative")

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
        self.last_detection_time = None
        self.watchdog_tripped = False
        self.manual_override_until = 0.0
        # Paho callbacks run on its network thread while the watchdog runs on
        # the service's main thread.  All PID/policy state transitions share
        # this lock so a fresh frame cannot race a timeout reset.
        self._state_lock = threading.RLock()

    def run(self):
        self.mqtt.subscribe(self.t_in, self._on_detections)
        self.mqtt.subscribe(self.t_command, self._on_control_command)
        with self._state_lock:
            # Also detect the case where Vision never produces its first frame.
            self.last_detection_time = time.perf_counter()
        logger.info("control_service up - subscribed to %s", self.t_in)
        try:
            while True:
                time.sleep(min(0.1, self.detection_timeout / 2.0))
                self._check_watchdog()
        except KeyboardInterrupt:
            logger.info("stopping")
        finally:
            self.mqtt.stop()

    def _on_detections(self, payload):
        with self._state_lock:
            self._handle_detections(payload)

    def _handle_detections(self, payload):
        if not isinstance(payload, dict):
            logger.warning("ignoring non-object detection payload")
            return

        source_timestamp = payload.get("timestamp")
        if source_timestamp is not None and self.max_detection_age > 0:
            try:
                source_timestamp = float(source_timestamp)
            except (TypeError, ValueError):
                logger.warning("ignoring detection payload with invalid timestamp")
                return
            if not math.isfinite(source_timestamp):
                logger.warning("ignoring detection payload with non-finite timestamp")
                return
            source_age = time.time() - source_timestamp
            if source_age > self.max_detection_age:
                logger.warning("ignoring stale detection payload (%.3fs old)", source_age)
                return

        now = time.perf_counter()
        dt = (1.0 / 30.0) if self.last_time is None else now - self.last_time
        self.last_time = now
        self.last_detection_time = now
        self.watchdog_tripped = False
        started_ns = time.perf_counter_ns()
        session_id = payload.get("session_id", "")

        if now < self.manual_override_until:
            pan, tilt = self.gimbal.hold()
            self.mqtt.publish(self.t_status, {
                "mode": "manual_override",
                "note": "manual servo command temporarily owns the gimbal",
                "pan": pan,
                "tilt": tilt,
                "pid_active": False,
                "session_id": session_id,
            })
            self._set_active(False)
            return

        detections = detections_from_payload(payload, self.default_frame_size)
        settings, target, mode = self.policy.decide(detections, now)
        status_note = settings.note
        if target is not None:
            pan, tilt = self.gimbal.track(target, dt)
            if now - self.last_command_time >= self.command_interval:
                self._publish_angles(pan, tilt, session_id)
                self.last_command_time = now
        else:
            if mode == "idle":
                # The configured number of missing ROI frames has elapsed.
                # Return both FS90s to their calibrated centres, send the
                # paired command once, then keep PWM enabled to hold position.
                status_note = "ROI lost; returned to calibrated centre"
                pan, tilt = self.gimbal.recenter()
                self._publish_angles(pan, tilt, session_id)
                self.last_command_time = now
            else:
                # Optional multi-frame loss debounce: retain the last pose
                # until ControlPolicy confirms that the ROI is really gone.
                pan, tilt = self.gimbal.hold()

        latency_ms = (time.perf_counter_ns() - started_ns) / 1e6
        self.mqtt.publish(self.t_status, {
            "mode": mode,
            "note": status_note,
            "pan": pan,
            "tilt": tilt,
            "pid_active": target is not None,
            "roi_center": self._status_roi_center(payload, target),
            "frame_center": self._status_frame_center(payload),
            "latency_ms": round(latency_ms, 3),
            "session_id": session_id,
        })

        self._set_active(mode == "tracking")

    def _on_control_command(self, payload):
        """Pause/reseed PID after a manual servo command from GPIO."""
        if not isinstance(payload, dict) or payload.get("type") != "manual_override":
            return
        try:
            duration = float(payload.get("duration", 0.0))
            servo = int(payload["servo"])
            angle = float(payload["angle"])
        except (KeyError, TypeError, ValueError):
            logger.warning("ignoring invalid manual override payload")
            return
        if not all(math.isfinite(value) for value in (duration, angle)):
            logger.warning("ignoring non-finite manual override payload")
            return

        with self._state_lock:
            duration = max(0.0, min(self.max_manual_override, duration))
            if servo == self.pan_ch:
                config = self.gimbal.pan_config
                self.gimbal.pan_angle = max(
                    config["min_angle"], min(config["max_angle"], angle)
                )
                self.gimbal.pan.reset()
                applied_angle = self.gimbal.pan_angle
            elif servo == self.tilt_ch:
                config = self.gimbal.tilt_config
                self.gimbal.tilt_angle = max(
                    config["min_angle"], min(config["max_angle"], angle)
                )
                self.gimbal.tilt.reset()
                applied_angle = self.gimbal.tilt_angle
            else:
                logger.warning("manual override references unknown servo %s", servo)
                return

            # GPIO may already have rejected an in-flight visual command after
            # starting its local override window. Forget both cached axes so
            # the first post-override PID/centre update is always sent as a
            # fresh paired command and restores software/hardware agreement.
            self.last_commanded_angles.clear()
            self.manual_override_until = max(
                self.manual_override_until, time.perf_counter() + duration
            )
            self.policy.reset()
            self._set_active(False)
            self.mqtt.publish(self.t_status, {
                "mode": "manual_override",
                "note": "PID paused after manual servo command",
                "pan": round(self.gimbal.pan_angle, 1),
                "tilt": round(self.gimbal.tilt_angle, 1),
                "pid_active": False,
                "manual_override_seconds": duration,
                "session_id": payload.get("session_id", ""),
            })

    def _set_active(self, active):
        if active != self.last_active:
            self.last_active = active
            self.mqtt.publish(self.t_power, {"active": active})
            logger.info("power_mode -> %s", "ACTIVE" if active else "IDLE")

    def _publish_angles(self, pan, tilt, session_id):
        angles = {
            # Servo angles are positive; half-up avoids Python's banker's
            # rounding delaying a 90.5-degree correction back to 90.
            self.pan_ch: int(float(pan) + 0.5),
            self.tilt_ch: int(float(tilt) + 0.5),
        }
        if all(
            self.last_commanded_angles.get(channel) == angle
            for channel, angle in angles.items()
        ):
            return
        self.last_commanded_angles.update(angles)
        self.mqtt.publish(self.t_gpio, {
            "type": "gimbal",
            "angles": {
                str(channel): angle for channel, angle in angles.items()
            },
            "source": "visual_pid",
            "session_id": session_id,
        })

    def _status_frame_center(self, payload):
        frame_size = payload.get("frame_size", self.default_frame_size)
        if isinstance(frame_size, dict):
            width = frame_size.get("width", self.default_frame_size[0])
            height = frame_size.get("height", self.default_frame_size[1])
        elif isinstance(frame_size, (list, tuple)) and len(frame_size) >= 2:
            width, height = frame_size[:2]
        else:
            width, height = self.default_frame_size
        try:
            width, height = float(width), float(height)
        except (TypeError, ValueError):
            width, height = map(float, self.default_frame_size)
        if not all(math.isfinite(value) and value > 0 for value in (width, height)):
            width, height = map(float, self.default_frame_size)
        return [round(width / 2.0, 3), round(height / 2.0, 3)]

    def _status_roi_center(self, payload, normalised_center):
        if normalised_center is None:
            return None
        frame_center = self._status_frame_center(payload)
        width, height = frame_center[0] * 2.0, frame_center[1] * 2.0
        return [
            round(normalised_center[0] * width, 3),
            round(normalised_center[1] * height, 3),
        ]

    def _check_watchdog(self, now=None):
        with self._state_lock:
            return self._check_watchdog_locked(now)

    def _check_watchdog_locked(self, now=None):
        """Recenter the gimbal if the Vision stream stops unexpectedly."""
        if self.last_detection_time is None or self.watchdog_tripped:
            return False
        now = time.perf_counter() if now is None else float(now)
        if now < self.manual_override_until:
            return False
        if now - self.last_detection_time < self.detection_timeout:
            return False

        self.watchdog_tripped = True
        self.last_time = None
        self.policy.reset()
        pan, tilt = self.gimbal.recenter()
        self._publish_angles(pan, tilt, "")
        self.last_command_time = now
        self.mqtt.publish(self.t_status, {
            "mode": "idle",
            "note": "vision stream timeout; returned to calibrated centre",
            "pan": pan,
            "tilt": tilt,
            "pid_active": False,
            "watchdog": True,
        })
        self._set_active(False)
        logger.warning("vision stream timed out; gimbal returned to centre")
        return True


if __name__ == "__main__":
    ControlService().run()
