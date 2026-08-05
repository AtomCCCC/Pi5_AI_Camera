"""Hardware-independent tests for the FS90 visual PID controller."""

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "pi5_assistant"
sys.path.insert(0, str(APP_ROOT))

from control_service.control_loop import (  # noqa: E402
    GimbalController,
    PID,
    detections_from_payload,
)
from gpio_service.servo_controller import ServoController  # noqa: E402


def axis_config(**overrides):
    config = {
        "kp": 60.0,
        "ki": 0.0,
        "kd": 0.0,
        "output_limit": 100.0,
        "integral_limit": 1.0,
        "derivative_filter": 1.0,
        "deadband": 0.0,
        "min_angle": 0.0,
        "max_angle": 180.0,
        "center_angle": 90.0,
        "direction": 1.0,
    }
    config.update(overrides)
    return config


class PIDTests(unittest.TestCase):
    def test_first_sample_has_no_derivative_kick(self):
        pid = PID(0, 0, 10, output_limit=100, derivative_filter=1.0)

        self.assertEqual(pid.update(0.5, 0.01), 0.0)
        self.assertEqual(pid.update(0.5, 0.01), 0.0)

    def test_output_is_limited_without_integral_windup(self):
        pid = PID(100, 10, 0, output_limit=5, integral_limit=10)

        for _ in range(20):
            self.assertEqual(pid.update(1.0, 0.1), 5.0)

        self.assertEqual(pid.integral, 0.0)


class GimbalControllerTests(unittest.TestCase):
    def test_angle_change_is_independent_of_frame_rate(self):
        slow = GimbalController(
            pan_config=axis_config(), tilt_config=axis_config(), dt_max=0.2
        )
        fast = GimbalController(
            pan_config=axis_config(), tilt_config=axis_config(), dt_max=0.2
        )

        for _ in range(10):
            slow.track((0.75, 0.5), 0.1)
        for _ in range(50):
            fast.track((0.75, 0.5), 0.02)

        self.assertAlmostEqual(slow.pan_angle, 105.0, places=6)
        self.assertAlmostEqual(fast.pan_angle, slow.pan_angle, places=6)

    def test_deadband_holds_position_and_resets_pid_state(self):
        config = axis_config(deadband=0.03, ki=2.0)
        gimbal = GimbalController(pan_config=config, tilt_config=config)
        gimbal.track((0.8, 0.5), 0.1)
        moved_angle = gimbal.pan_angle

        gimbal.track((0.52, 0.5), 0.1)

        self.assertEqual(gimbal.pan_angle, moved_angle)
        self.assertEqual(gimbal.pan.integral, 0.0)
        self.assertIsNone(gimbal.pan.prev_error)

    def test_direction_and_mechanical_limits_are_applied(self):
        reversed_axis = axis_config(
            kp=200.0,
            output_limit=500.0,
            direction=-1,
            min_angle=80,
            max_angle=100,
        )
        gimbal = GimbalController(
            pan_config=reversed_axis,
            tilt_config=axis_config(),
            dt_max=1.0,
        )

        gimbal.track((1.0, 0.5), 1.0)

        self.assertEqual(gimbal.pan_angle, 80.0)


class DetectionPayloadTests(unittest.TestCase):
    def test_pixel_boxes_use_payload_frame_dimensions(self):
        detections = detections_from_payload({
            "frame_size": {"width": 640, "height": 320},
            "coordinate_space": "pixels",
            "detections": [{
                "name": "person",
                "confidence": 0.9,
                "bbox": [320, 80, 64, 32],
            }],
        })

        self.assertEqual(len(detections), 1)
        self.assertAlmostEqual(detections[0].center[0], 0.55)
        self.assertAlmostEqual(detections[0].center[1], 0.30)

    def test_normalised_boxes_remain_supported(self):
        detections = detections_from_payload({
            "detections": [{
                "label": "person",
                "confidence": 0.8,
                "bbox": [0.1, 0.2, 0.2, 0.4],
            }],
        })

        self.assertAlmostEqual(detections[0].center[0], 0.2)
        self.assertAlmostEqual(detections[0].center[1], 0.4)

    def test_malformed_boxes_are_ignored(self):
        detections = detections_from_payload({
            "detections": [
                {"bbox": [0, 0, 0, 5], "confidence": 1.0},
                {"bbox": ["bad", 0, 2, 2], "confidence": 1.0},
            ]
        })

        self.assertEqual(detections, [])


class ServoCalibrationTests(unittest.TestCase):
    def test_safety_angle_limit_does_not_remap_pulse_calibration(self):
        servo = ServoController._normalise_config(0, {
            "freq": 50,
            "min_pulse": 544,
            "center_pulse": 1500,
            "max_pulse": 2400,
            "min_angle": 10,
            "max_angle": 170,
            "center_angle": 90,
        })

        self.assertEqual(ServoController._angle_to_pulse(servo, 90), 1_500_000)
        self.assertGreater(ServoController._angle_to_pulse(servo, 10), 544_000)

    def test_driver_centres_and_clamps_using_a_fake_pwm_chip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pwm_chip = Path(temp_dir)
            (pwm_chip / "export").write_text("")
            (pwm_chip / "unexport").write_text("")
            for channel in (0, 2):
                channel_path = pwm_chip / f"pwm{channel}"
                channel_path.mkdir()
                (channel_path / "enable").write_text("0")
                (channel_path / "period").write_text("")
                (channel_path / "duty_cycle").write_text("")

            config = {
                "servo": {
                    "servo1": {
                        "pin": 18,
                        "min_pulse": 544,
                        "center_pulse": 1500,
                        "max_pulse": 2400,
                        "min_angle": 10,
                        "max_angle": 170,
                    },
                    "servo2": {
                        "pin": 12,
                        "min_pulse": 544,
                        "center_pulse": 1500,
                        "max_pulse": 2400,
                        "min_angle": 10,
                        "max_angle": 170,
                    },
                }
            }

            controller = ServoController(config, pwm_chip=str(pwm_chip))
            self.assertEqual(
                (pwm_chip / "pwm2" / "duty_cycle").read_text(), "1500000"
            )

            controller.set_angle(1, -30)
            expected = ServoController._angle_to_pulse(
                controller._servos["servo1"], 10
            )
            self.assertEqual(
                (pwm_chip / "pwm2" / "duty_cycle").read_text(), str(expected)
            )
            controller.cleanup()
            self.assertEqual((pwm_chip / "pwm2" / "enable").read_text(), "0")


if __name__ == "__main__":
    unittest.main()
