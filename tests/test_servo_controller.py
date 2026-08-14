"""Tests for FS90 safety limits without touching Raspberry Pi GPIO."""

import tempfile
import unittest
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "pi5_assistant"
sys.path.insert(0, str(APP_ROOT))

from gpio_service.servo_controller import ServoController  # noqa: E402


class ServoControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.chip = Path(self.temp_dir.name) / "pwmchip0"
        self.chip.mkdir()
        (self.chip / "export").write_text("", encoding="ascii")
        (self.chip / "unexport").write_text("", encoding="ascii")
        for channel in (0, 1):
            path = self.chip / f"pwm{channel}"
            path.mkdir()
            (path / "enable").write_text("1", encoding="ascii")
            (path / "period").write_text("0", encoding="ascii")
            (path / "duty_cycle").write_text("0", encoding="ascii")
            (path / "polarity").write_text("inversed", encoding="ascii")

        self.config = {
            "pwm": {
                "chip": str(self.chip),
                "lock_file": "",
                "gpio_to_channel": {12: 0, 13: 1},
            },
            "servo": {
                "servo1": {
                    "pin": 13, "freq": 50,
                    "min_pulse": 900, "max_pulse": 2100,
                    "pulse_min_angle": 0, "pulse_max_angle": 180,
                    "min_angle": 20, "max_angle": 160,
                    "center_angle": 90,
                },
                "servo2": {
                    "pin": 12, "freq": 50,
                    "min_pulse": 900, "max_pulse": 2100,
                    "pulse_min_angle": 0, "pulse_max_angle": 180,
                    "min_angle": 20, "max_angle": 160,
                    "center_angle": 90,
                },
            },
        }
        self.controller = ServoController(self.config)

    def tearDown(self):
        self.controller.cleanup()
        self.temp_dir.cleanup()

    def _attribute(self, channel, name):
        return (self.chip / f"pwm{channel}" / name).read_text(encoding="ascii")

    def test_startup_centres_both_servos_at_50_hz(self):
        self.assertEqual(self._attribute(1, "period"), "20000000")
        self.assertEqual(self._attribute(0, "period"), "20000000")
        self.assertEqual(self._attribute(1, "duty_cycle"), "1500000")
        self.assertEqual(self._attribute(0, "duty_cycle"), "1500000")
        self.assertEqual(self._attribute(1, "enable"), "1")
        self.assertEqual(self._attribute(1, "polarity"), "normal")

    def test_gpio_layer_clamps_to_final_mechanical_limits(self):
        self.assertEqual(self.controller.set_angle(1, 0), 20)
        self.assertEqual(self._attribute(1, "duty_cycle"), "1033333")
        self.assertEqual(self.controller.set_angle(1, 180), 160)
        self.assertEqual(self._attribute(1, "duty_cycle"), "1966667")

    def test_paired_update_applies_both_axes_under_one_command(self):
        applied = self.controller.set_angles({"1": 50, "2": 130})
        self.assertEqual(applied, {"servo1": 50, "servo2": 130})
        self.assertNotEqual(self._attribute(1, "duty_cycle"), "1500000")
        self.assertNotEqual(self._attribute(0, "duty_cycle"), "1500000")

    def test_return_to_center_keeps_both_pwm_channels_enabled(self):
        self.controller.set_angles({"1": 40, "2": 140})
        self.controller.set_angles({"1": 90, "2": 90})

        self.assertEqual(self._attribute(1, "duty_cycle"), "1500000")
        self.assertEqual(self._attribute(0, "duty_cycle"), "1500000")
        self.assertEqual(self._attribute(1, "enable"), "1")
        self.assertEqual(self._attribute(0, "enable"), "1")

    def test_rejects_non_finite_angle_before_writing(self):
        before = self._attribute(1, "duty_cycle")
        with self.assertRaises(ValueError):
            self.controller.set_angle(1, float("nan"))
        self.assertEqual(self._attribute(1, "duty_cycle"), before)

    def test_paired_command_is_validated_before_either_axis_is_written(self):
        before_pan = self._attribute(1, "duty_cycle")
        before_tilt = self._attribute(0, "duty_cycle")
        with self.assertRaises(ValueError):
            self.controller.set_angles({"1": 50, "2": float("nan")})
        self.assertEqual(self._attribute(1, "duty_cycle"), before_pan)
        self.assertEqual(self._attribute(0, "duty_cycle"), before_tilt)

    def test_cleanup_disables_pwm(self):
        self.controller.cleanup()
        self.assertEqual(self._attribute(1, "enable"), "0")
        self.assertEqual(self._attribute(0, "enable"), "0")


if __name__ == "__main__":
    unittest.main()
