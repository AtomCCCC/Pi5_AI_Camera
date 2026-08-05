"""Thread-safe FS90 servo control using Raspberry Pi 5 kernel PWM."""

import logging
import math
import os
import threading
import time


logger = logging.getLogger(__name__)

PWM_CHIP = "/sys/class/pwm/pwmchip0"
DEFAULT_PERIOD_NS = 20_000_000  # 50 Hz
GPIO_TO_PWM = {18: 2, 12: 0}


class ServoController:
    """Control two positional hobby servos through hardware PWM."""

    def __init__(self, config: dict, pwm_chip: str = PWM_CHIP):
        self.pwm_chip = pwm_chip
        self._lock = threading.Lock()
        self._servos = {}

        for name, cfg in config["servo"].items():
            gpio = int(cfg["pin"])
            channel = GPIO_TO_PWM.get(gpio)
            if channel is None:
                raise ValueError(f"No hardware PWM channel for GPIO{gpio}")
            if any(servo["channel"] == channel for servo in self._servos.values()):
                raise ValueError(f"PWM channel {channel} is configured more than once")

            servo = self._normalise_config(channel, cfg)
            self._servos[name] = servo
            initial_pulse = self._angle_to_pulse(servo, servo["center_angle"])
            self._init_pwm(channel, servo["period_ns"], initial_pulse)
            logger.info("%s: GPIO%s -> pwm%s (hardware PWM)", name, gpio, channel)

    @staticmethod
    def _normalise_config(channel, cfg):
        frequency = float(cfg.get("freq", 50))
        if frequency <= 0:
            raise ValueError("servo frequency must be positive")
        servo = {
            "channel": channel,
            "period_ns": round(1_000_000_000 / frequency),
            "min_pulse_ns": round(float(cfg.get("min_pulse", 544)) * 1000),
            "center_pulse_ns": round(float(cfg.get("center_pulse", 1500)) * 1000),
            "max_pulse_ns": round(float(cfg.get("max_pulse", 2400)) * 1000),
            "min_angle": float(cfg.get("min_angle", 0)),
            "max_angle": float(cfg.get("max_angle", 180)),
            "center_angle": float(cfg.get("center_angle", 90)),
            "pulse_min_angle": float(cfg.get("pulse_min_angle", 0)),
            "pulse_center_angle": float(cfg.get("pulse_center_angle", 90)),
            "pulse_max_angle": float(cfg.get("pulse_max_angle", 180)),
            "last_angle": None,
        }
        if servo["min_pulse_ns"] >= servo["max_pulse_ns"]:
            raise ValueError("servo min_pulse must be less than max_pulse")
        if not (
            servo["min_pulse_ns"]
            < servo["center_pulse_ns"]
            < servo["max_pulse_ns"]
        ):
            raise ValueError("servo center_pulse must be between min_pulse and max_pulse")
        if servo["max_pulse_ns"] >= servo["period_ns"]:
            raise ValueError("servo max_pulse must be shorter than the PWM period")
        if servo["min_angle"] >= servo["max_angle"]:
            raise ValueError("servo min_angle must be less than max_angle")
        if servo["pulse_min_angle"] >= servo["pulse_max_angle"]:
            raise ValueError("servo pulse_min_angle must be less than pulse_max_angle")
        if not (
            servo["pulse_min_angle"]
            < servo["pulse_center_angle"]
            < servo["pulse_max_angle"]
        ):
            raise ValueError(
                "servo pulse_center_angle must be between its calibration limits"
            )
        if (
            servo["min_angle"] < servo["pulse_min_angle"]
            or servo["max_angle"] > servo["pulse_max_angle"]
        ):
            raise ValueError("servo angle limits must be inside pulse calibration limits")
        if not servo["min_angle"] <= servo["center_angle"] <= servo["max_angle"]:
            raise ValueError("servo center_angle must be inside its angle limits")
        return servo

    def _channel_path(self, channel):
        return os.path.join(self.pwm_chip, f"pwm{channel}")

    def _write(self, path, value):
        with open(path, "w") as pwm_file:
            pwm_file.write(str(value))

    def _init_pwm(self, channel, period_ns, initial_pulse_ns):
        path = self._channel_path(channel)
        if not os.path.exists(path):
            self._write(os.path.join(self.pwm_chip, "export"), channel)
            for _ in range(20):
                if os.path.exists(path):
                    break
                time.sleep(0.01)
            else:
                raise RuntimeError(f"PWM channel {channel} was not exported")

        enable_path = os.path.join(path, "enable")
        try:
            with open(enable_path) as enable_file:
                enabled = enable_file.read().strip() == "1"
        except OSError:
            enabled = False
        if enabled:
            self._write(enable_path, 0)

        self._write(os.path.join(path, "period"), period_ns)
        self._write(os.path.join(path, "duty_cycle"), initial_pulse_ns)
        self._write(enable_path, 1)

    @staticmethod
    def _angle_to_pulse(servo, angle):
        if angle <= servo["pulse_center_angle"]:
            ratio = (angle - servo["pulse_min_angle"]) / (
                servo["pulse_center_angle"] - servo["pulse_min_angle"]
            )
            pulse = servo["min_pulse_ns"] + ratio * (
                servo["center_pulse_ns"] - servo["min_pulse_ns"]
            )
        else:
            ratio = (angle - servo["pulse_center_angle"]) / (
                servo["pulse_max_angle"] - servo["pulse_center_angle"]
            )
            pulse = servo["center_pulse_ns"] + ratio * (
                servo["max_pulse_ns"] - servo["center_pulse_ns"]
            )
        return round(pulse)

    def set_angle(self, servo_num: int, angle: float) -> None:
        servo_key = f"servo{servo_num}"
        servo = self._servos.get(servo_key)
        if servo is None:
            raise ValueError(f"Invalid servo number: {servo_num}")
        try:
            angle = float(angle)
        except (TypeError, ValueError) as exc:
            raise ValueError("servo angle must be numeric") from exc
        if not math.isfinite(angle):
            raise ValueError("servo angle must be finite")

        angle = max(servo["min_angle"], min(servo["max_angle"], angle))
        pulse = self._angle_to_pulse(servo, angle)
        duty_path = os.path.join(
            self._channel_path(servo["channel"]), "duty_cycle"
        )
        with self._lock:
            self._write(duty_path, pulse)
            servo["last_angle"] = angle
        logger.debug("Servo %s -> %.1f degrees", servo_num, angle)

    def release(self, servo_num: int) -> None:
        """Return a servo to its configured centre position."""
        servo = self._servos.get(f"servo{servo_num}")
        if servo is not None:
            self.set_angle(servo_num, servo["center_angle"])

    def cleanup(self) -> None:
        with self._lock:
            for name, servo in self._servos.items():
                channel = servo["channel"]
                try:
                    self._write(
                        os.path.join(self._channel_path(channel), "enable"), 0
                    )
                    self._write(os.path.join(self.pwm_chip, "unexport"), channel)
                    logger.info("Released %s (pwm%s)", name, channel)
                except OSError as exc:
                    logger.warning("Cleanup %s error: %s", name, exc)
