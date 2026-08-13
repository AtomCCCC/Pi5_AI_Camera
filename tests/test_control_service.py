"""Hardware-independent tests for the visual PID control service."""

import math
import sys
import threading
import time
import types
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "pi5_assistant"
sys.path.insert(0, str(APP_ROOT))


try:
    import paho.mqtt.client  # noqa: F401
except ImportError:
    paho_stub = types.ModuleType("paho")
    mqtt_package_stub = types.ModuleType("paho.mqtt")
    mqtt_client_stub = types.ModuleType("paho.mqtt.client")
    mqtt_client_stub.Client = object
    paho_stub.mqtt = mqtt_package_stub
    mqtt_package_stub.client = mqtt_client_stub
    sys.modules["paho"] = paho_stub
    sys.modules["paho.mqtt"] = mqtt_package_stub
    sys.modules["paho.mqtt.client"] = mqtt_client_stub

try:
    import yaml  # noqa: F401
except ImportError:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda _stream: {}
    sys.modules["yaml"] = yaml_stub


from control_service.control_loop import (  # noqa: E402
    ControlPolicy,
    Detection,
    GimbalController,
    PID,
    detections_from_payload,
)
from control_service.main import ControlService  # noqa: E402


class FakeMQTT:
    def __init__(self):
        self.messages = []
        self.subscriptions = []
        self.stopped = False

    def publish(self, topic, payload):
        self.messages.append((topic, payload))

    def subscribe(self, topic, callback):
        self.subscriptions.append((topic, callback))

    def stop(self):
        self.stopped = True


class ConfigurationContractTests(unittest.TestCase):
    def test_vision_control_and_gpio_safety_settings_stay_aligned(self):
        vision = (
            APP_ROOT / "vision_service" / "config.yaml"
        ).read_text(encoding="utf-8")
        control = (
            APP_ROOT / "control_service" / "config.yaml"
        ).read_text(encoding="utf-8")
        gpio = (
            APP_ROOT / "gpio_service" / "config.yaml"
        ).read_text(encoding="utf-8")

        labels = 'target_labels: ["person", "face"]'
        self.assertIn(labels, vision)
        self.assertIn(labels, control)
        self.assertEqual(control.count("min_angle: 20"), 2)
        self.assertEqual(control.count("max_angle: 160"), 2)
        self.assertEqual(gpio.count("min_angle: 20"), 2)
        self.assertEqual(gpio.count("max_angle: 160"), 2)
        self.assertIn("12: 0", gpio)
        self.assertIn("13: 1", gpio)
        self.assertIn("pin: 13", gpio)
        self.assertIn("pin: 12", gpio)
        self.assertNotIn("pin: 18", gpio)
        self.assertIn(
            'start_service control "$PYTHON" -m control_service.main',
            (APP_ROOT / "run_all.sh").read_text(encoding="utf-8"),
        )


class DetectionPayloadTests(unittest.TestCase):
    def test_pixel_box_uses_non_square_source_dimensions(self):
        result = detections_from_payload({
            "frame_size": {"width": 200, "height": 100},
            "coordinate_space": "pixels",
            "detections": [{
                "name": "person",
                "confidence": 0.9,
                "bbox": [100, 25, 40, 20],
            }],
        })

        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].x, 0.5)
        self.assertAlmostEqual(result[0].y, 0.25)
        self.assertAlmostEqual(result[0].w, 0.2)
        self.assertAlmostEqual(result[0].h, 0.2)
        self.assertEqual(result[0].center, (0.6, 0.35))

    def test_explicit_tracking_target_matches_visible_roi(self):
        result = detections_from_payload({
            "frame_size": [100, 100],
            "coordinate_space": "pixels",
            "tracking_target": {
                "name": "person", "confidence": 0.8,
                "bbox": [70, 40, 20, 20],
            },
            "detections": [{
                "name": "car", "confidence": 0.99,
                "bbox": [0, 40, 20, 20],
            }],
        })

        self.assertEqual([detection.label for detection in result], ["person"])
        self.assertAlmostEqual(result[0].center[0], 0.8)
        self.assertAlmostEqual(result[0].center[1], 0.5)

    def test_none_tracking_target_means_no_valid_roi(self):
        result = detections_from_payload({
            "tracking_target": None,
            "detections": [{
                "name": "car", "confidence": 0.99,
                "bbox": [0.1, 0.1, 0.2, 0.2],
            }],
        })
        self.assertEqual(result, [])

    def test_explicit_roi_center_is_the_pid_coordinate(self):
        result = detections_from_payload({
            "frame_size": [640, 640],
            "coordinate_space": "pixels",
            "tracking_target": {
                "name": "person",
                "confidence": 0.9,
                # Deliberately disagree with the explicit control point.
                "bbox": [0, 0, 100, 100],
                "roi_center": [320, 320],
            },
        })

        self.assertEqual(result[0].center, (0.5, 0.5))

    def test_invalid_explicit_roi_center_falls_back_to_bbox(self):
        result = detections_from_payload({
            "frame_size": [640, 640],
            "coordinate_space": "pixels",
            "tracking_target": {
                "name": "person",
                "confidence": 0.9,
                "bbox": [110, 110, 100, 100],
                "roi_center": [float("nan"), 320],
            },
        })

        self.assertEqual(result[0].center, (0.25, 0.25))

    def test_non_finite_frame_size_and_out_of_range_center_are_safe(self):
        result = detections_from_payload({
            "frame_size": [float("inf"), float("nan")],
            "coordinate_space": "pixels",
            "tracking_target": {
                "name": "person",
                "confidence": 0.9,
                "bbox": [270, 280, 100, 80],
                "roi_center": [10000, -10000],
            },
        })

        # Falls back to the default 640x640 frame and bbox centre, rather than
        # clamping a corrupt explicit point into a maximum-speed command.
        self.assertEqual(result[0].center, (0.5, 0.5))

    def test_invalid_and_out_of_frame_boxes_are_ignored_or_clipped(self):
        result = detections_from_payload({
            "coordinate_space": "normalized",
            "detections": [
                {"bbox": [0, 0, 0, 1], "confidence": 1},
                {"bbox": [math.nan, 0, 1, 1], "confidence": 1},
                {"bbox": [0.9, 0.8, 0.5, 0.5], "confidence": 0.8},
            ],
        })
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].w, 0.1)
        self.assertAlmostEqual(result[0].h, 0.2)


class PIDTests(unittest.TestCase):
    def test_first_sample_has_no_derivative_kick(self):
        pid = PID(0, 0, 10, output_limit=100, derivative_filter=1)
        self.assertEqual(pid.update(0.4, 0.01), 0)

    def test_saturation_does_not_wind_up_integral(self):
        pid = PID(100, 10, 0, output_limit=1, integral_limit=10)
        for _ in range(20):
            self.assertEqual(pid.update(1, 0.1), 1)
        self.assertEqual(pid.integral, 0)

    def test_reset_clears_all_state(self):
        pid = PID(1, 1, 1, output_limit=10)
        pid.update(0.2, 0.1)
        pid.update(0.1, 0.1)
        pid.reset()
        self.assertIsNone(pid.prev_error)
        self.assertEqual(pid.integral, 0)
        self.assertEqual(pid.derivative, 0)


def axis_config(**overrides):
    config = {
        "kp": 20,
        "ki": 0,
        "kd": 0,
        "output_limit": 100,
        "integral_limit": 1,
        "derivative_filter": 0.2,
        "deadband": 0,
        "min_angle": 20,
        "max_angle": 160,
        "center_angle": 90,
        "direction": 1,
    }
    config.update(overrides)
    return config


class GimbalControllerTests(unittest.TestCase):
    def test_center_deadband_holds_angles(self):
        config = axis_config(deadband=0.03)
        gimbal = GimbalController(pan_config=config, tilt_config=config)
        self.assertEqual(gimbal.track((0.52, 0.48), 0.1), (90.0, 90.0))

    def test_axis_direction_can_be_reversed(self):
        forward = GimbalController(
            pan_config=axis_config(), tilt_config=axis_config()
        )
        reverse = GimbalController(
            pan_config=axis_config(direction=-1), tilt_config=axis_config()
        )
        self.assertGreater(forward.track((0.8, 0.5), 0.1)[0], 90)
        self.assertLess(reverse.track((0.8, 0.5), 0.1)[0], 90)

    def test_mechanical_limit_freezes_integral(self):
        pan = axis_config(
            kp=10, ki=5, center_angle=160, min_angle=20, max_angle=160
        )
        gimbal = GimbalController(pan_config=pan, tilt_config=axis_config())
        self.assertEqual(gimbal.track((0.9, 0.5), 0.1)[0], 160)
        self.assertEqual(gimbal.pan.integral, 0)

    def test_velocity_form_is_consistent_across_frame_rates(self):
        slow = GimbalController(
            pan_config=axis_config(), tilt_config=axis_config()
        )
        fast = GimbalController(
            pan_config=axis_config(), tilt_config=axis_config()
        )
        for _ in range(10):
            slow.track((0.7, 0.5), 0.1)
        for _ in range(20):
            fast.track((0.7, 0.5), 0.05)
        self.assertAlmostEqual(slow.pan_angle, fast.pan_angle, places=6)

    def test_simple_visual_plant_converges_without_exceeding_limits(self):
        gimbal = GimbalController(
            pan_config=axis_config(kp=30, deadband=0.01),
            tilt_config=axis_config(),
        )
        target_x = 0.8
        for _ in range(300):
            gimbal.track((target_x, 0.5), 0.05)
            target_x = 0.8 - (gimbal.pan_angle - 90) / 100
        self.assertLess(abs(target_x - 0.5), 0.025)
        self.assertGreaterEqual(gimbal.pan_angle, 20)
        self.assertLessEqual(gimbal.pan_angle, 160)

    def test_recenter_uses_configured_centres_and_resets_pid(self):
        gimbal = GimbalController(
            pan_config=axis_config(center_angle=88),
            tilt_config=axis_config(center_angle=93),
        )
        gimbal.track((0.8, 0.2), 0.1)
        self.assertIsNotNone(gimbal.pan.prev_error)

        self.assertEqual(gimbal.recenter(), (88.0, 93.0))
        self.assertIsNone(gimbal.pan.prev_error)
        self.assertIsNone(gimbal.tilt.prev_error)


class ControlPolicyTests(unittest.TestCase):
    def test_filters_class_and_confidence_then_debounces_loss(self):
        policy = ControlPolicy(
            min_switch_interval=0,
            empty_frames_to_idle=2,
            confidence_threshold=0.65,
            target_labels=["person"],
        )
        detections = [
            Detection("car", 0.99, 0.1, 0.1, 0.2, 0.2),
            Detection("person", 0.9, 0.6, 0.4, 0.2, 0.2),
        ]
        _settings, target, mode = policy.decide(detections, now=1)
        self.assertEqual(mode, "tracking")
        self.assertEqual(target, (0.7, 0.5))
        self.assertEqual(policy.decide([], now=2)[2], "tracking")
        self.assertEqual(policy.decide([], now=3)[2], "idle")


class ControlServiceTests(unittest.TestCase):
    def setUp(self):
        self.mqtt = FakeMQTT()
        self.service = ControlService.__new__(ControlService)
        self.service.mqtt = self.mqtt
        self.service.t_gpio = "gpio/command"
        self.service.t_status = "control/status"
        self.service.t_power = "control/power_mode"
        self.service.t_command = "control/command"
        self.service.pan_ch = 1
        self.service.tilt_ch = 2
        self.service.default_frame_size = (640, 640)
        self.service.command_interval = 0
        self.service.detection_timeout = 0.75
        self.service.max_detection_age = 0.5
        self.service.max_manual_override = 30
        self.service.policy = ControlPolicy(
            min_switch_interval=0,
            empty_frames_to_idle=1,
            confidence_threshold=0.65,
            target_labels=["person"],
        )
        self.service.gimbal = GimbalController()
        self.service.last_time = None
        self.service.last_command_time = 0
        self.service.last_commanded_angles = {}
        self.service.last_active = None
        self.service.last_detection_time = None
        self.service.watchdog_tripped = False
        self.service.manual_override_until = 0
        self.service._state_lock = threading.RLock()

    def _payload(self):
        return {
            "timestamp": time.time(),
            "frame_size": {"width": 100, "height": 100},
            "coordinate_space": "pixels",
            "tracking_target": {
                "name": "person",
                "confidence": 0.9,
                "bbox": [70, 40, 20, 20],
                "roi_center": [80, 50],
            },
        }

    def test_detection_publishes_one_paired_gimbal_command(self):
        self.service._on_detections(self._payload())

        commands = [
            payload for topic, payload in self.mqtt.messages
            if topic == self.service.t_gpio
        ]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["type"], "gimbal")
        self.assertEqual(set(commands[0]["angles"]), {"1", "2"})
        self.assertGreater(commands[0]["angles"]["1"], 90)

        statuses = [
            payload for topic, payload in self.mqtt.messages
            if topic == self.service.t_status
        ]
        self.assertTrue(statuses[-1]["pid_active"])
        self.assertEqual(statuses[-1]["roi_center"], [80.0, 50.0])
        self.assertEqual(statuses[-1]["frame_center"], [50.0, 50.0])

    def test_640_frame_center_has_zero_pid_error(self):
        payload = self._payload()
        payload["frame_size"] = {"width": 640, "height": 640}
        payload["tracking_target"]["bbox"] = [0, 0, 100, 100]
        payload["tracking_target"]["roi_center"] = [320, 320]

        self.service._on_detections(payload)

        commands = [
            message for topic, message in self.mqtt.messages
            if topic == self.service.t_gpio
        ]
        self.assertEqual(commands[-1]["angles"], {"1": 90, "2": 90})
        statuses = [
            message for topic, message in self.mqtt.messages
            if topic == self.service.t_status
        ]
        self.assertEqual(statuses[-1]["roi_center"], [320.0, 320.0])
        self.assertEqual(statuses[-1]["frame_center"], [320.0, 320.0])

    def test_roi_loss_recenters_once_and_holds(self):
        self.service._on_detections(self._payload())
        empty = self._payload()
        empty["tracking_target"] = None

        self.service._on_detections(empty)
        commands = [
            message for topic, message in self.mqtt.messages
            if topic == self.service.t_gpio
        ]
        self.assertEqual(commands[-1]["angles"], {"1": 90, "2": 90})
        self.assertEqual(
            (self.service.gimbal.pan_angle, self.service.gimbal.tilt_angle),
            (90.0, 90.0),
        )
        command_count = len(commands)

        self.service._on_detections(empty)
        commands = [
            message for topic, message in self.mqtt.messages
            if topic == self.service.t_gpio
        ]
        self.assertEqual(len(commands), command_count)
        statuses = [
            message for topic, message in self.mqtt.messages
            if topic == self.service.t_status
        ]
        self.assertEqual(statuses[-1]["mode"], "idle")
        self.assertFalse(statuses[-1]["pid_active"])

    def test_stale_feedback_is_ignored(self):
        payload = self._payload()
        payload["timestamp"] -= 5
        self.service._on_detections(payload)
        self.assertEqual(self.mqtt.messages, [])

    def test_non_finite_feedback_timestamp_is_ignored(self):
        payload = self._payload()
        payload["timestamp"] = float("nan")
        self.service._on_detections(payload)
        self.assertEqual(self.mqtt.messages, [])

    def test_watchdog_resets_pid_and_reports_idle(self):
        self.service._on_detections(self._payload())
        last_seen = self.service.last_detection_time
        tripped = self.service._check_watchdog(
            last_seen + self.service.detection_timeout + 0.01
        )

        self.assertTrue(tripped)
        self.assertTrue(self.service.watchdog_tripped)
        self.assertEqual(self.service.policy.mode, "idle")
        watchdog_statuses = [
            payload for topic, payload in self.mqtt.messages
            if topic == self.service.t_status and payload.get("watchdog")
        ]
        self.assertEqual(len(watchdog_statuses), 1)
        self.assertFalse(watchdog_statuses[0]["pid_active"])
        commands = [
            payload for topic, payload in self.mqtt.messages
            if topic == self.service.t_gpio
        ]
        self.assertEqual(commands[-1]["angles"], {"1": 90, "2": 90})

    def test_manual_command_reseeds_angle_and_temporarily_pauses_pid(self):
        self.service._on_control_command({
            "type": "manual_override",
            "duration": 3,
            "servo": 1,
            "angle": 120,
        })
        self.assertEqual(self.service.gimbal.pan_angle, 120)
        self.assertGreater(
            self.service.manual_override_until, time.perf_counter()
        )

        before_commands = len([
            payload for topic, payload in self.mqtt.messages
            if topic == self.service.t_gpio
        ])
        self.service._on_detections(self._payload())
        after_commands = len([
            payload for topic, payload in self.mqtt.messages
            if topic == self.service.t_gpio
        ])
        self.assertEqual(after_commands, before_commands)
        self.assertEqual(self.service.gimbal.pan_angle, 120)

        # Once the override expires, the cache must not suppress the fresh
        # paired command used to resynchronise both physical axes.
        self.service.manual_override_until = 0
        self.service._on_detections(self._payload())
        resumed_commands = [
            payload for topic, payload in self.mqtt.messages
            if topic == self.service.t_gpio
        ]
        self.assertEqual(len(resumed_commands), before_commands + 1)
        self.assertEqual(set(resumed_commands[-1]["angles"]), {"1", "2"})


if __name__ == "__main__":
    unittest.main()
