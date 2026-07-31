"""
control_service/main.py  -  Role 4 as an MQTT service
================================================================
Wraps the existing ControlPolicy + PID (logic UNCHANGED) as a service on the
team's MQTT bus. In the mesh, the Vision service owns the camera and NPU and
publishes detections; the GPIO service owns the servos. This service is the
decision layer in between — the one thing no other service provides.

Subscribes:  vision/detect_result
Publishes:   gpio/command      one message per servo: {type:"servo", servo, angle, session_id}
             control/status    {mode, note, latency_ms, session_id}   (dashboard)
             control/power_mode {active: bool}   (lets Vision throttle framerate -> power saving)

Run:  python -m control_service.main
"""

import os
import sys
import time
import logging
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pi5_assistant.mqtt_client import MQTTClient
from control_service.control_loop import ControlPolicy, GimbalController, Detection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("control_service")

# The Vision service reports bounding boxes in PIXELS relative to its YOLO
# input frame. We divide by this to normalise to 0..1 for the control policy.
# NOTE: confirm 640x640 with the Vision service owner (high-motion mode is 640x320).
FRAME_W, FRAME_H = 640, 640


class ControlService:
    def __init__(self, config_path=None):
        config_path = config_path or Path(__file__).with_name("config.yaml")
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

        m = self.cfg["mqtt"]
        self.t_in     = m["topic_detect_result"]
        self.t_gpio   = m["topic_gpio_command"]
        self.t_status = m["topic_status"]
        self.t_power  = m["topic_power_mode"]
        self.pan_ch   = self.cfg["servo"]["pan_channel"]
        self.tilt_ch  = self.cfg["servo"]["tilt_channel"]

        self.mqtt = MQTTClient("control", m["broker"], m["port"])
        self.policy = ControlPolicy()
        self.gimbal = GimbalController()     # emits over MQTT, no direct servo
        self.last_time = time.perf_counter()
        self.last_active = None

    def run(self):
        self.mqtt.subscribe(self.t_in, self._on_detections)
        logger.info("control_service up — subscribed to %s", self.t_in)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("stopping")
        finally:
            self.mqtt.stop()

    def _on_detections(self, payload):
        now = time.perf_counter()
        dt = now - self.last_time
        self.last_time = now
        t0 = time.perf_counter_ns()

        # incoming JSON -> your Detection objects
        # Vision service fields: "name" (not "label") and bbox in PIXELS [x,y,w,h].
        detections = []
        for d in payload.get("detections", []):
            box = d.get("bbox", [0, 0, 0, 0])
            detections.append(Detection(
                label=d.get("name", d.get("label", d.get("class", ""))),
                confidence=float(d.get("confidence", 0.0)),
                x=float(box[0]) / FRAME_W,
                y=float(box[1]) / FRAME_H,
                w=float(box[2]) / FRAME_W,
                h=float(box[3]) / FRAME_H,
            ))

        # YOUR unchanged decision + PID
        settings, target, mode = self.policy.decide(detections, now)
        if target is not None:
            pan, tilt = self.gimbal.track(target, dt)
        else:
            pan, tilt = self.gimbal.hold()

        latency_ms = (time.perf_counter_ns() - t0) / 1e6
        sid = payload.get("session_id", "")

        # one message per servo — matches gpio_service format exactly
        self.mqtt.publish(self.t_gpio, {"type": "servo", "servo": self.pan_ch,
                                        "angle": round(pan), "session_id": sid})
        self.mqtt.publish(self.t_gpio, {"type": "servo", "servo": self.tilt_ch,
                                        "angle": round(tilt), "session_id": sid})

        # status for the dashboard
        self.mqtt.publish(self.t_status, {
            "mode": mode, "note": settings.note,
            "pan": pan, "tilt": tilt,
            "latency_ms": round(latency_ms, 3), "session_id": sid,
        })

        # power hint: only publish when active/idle CHANGES, so Vision can throttle
        active = (mode == "tracking")
        if active != self.last_active:
            self.last_active = active
            self.mqtt.publish(self.t_power, {"active": active})
            logger.info("power_mode -> %s", "ACTIVE" if active else "IDLE")


if __name__ == "__main__":
    ControlService().run()
