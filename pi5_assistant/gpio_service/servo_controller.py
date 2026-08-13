"""FS90 servo control for Raspberry Pi 5 kernel hardware PWM.

The controller owns all sysfs writes, clamps every command to the configured
mechanical range, and serialises paired pan/tilt updates so stale worker
threads cannot overwrite newer PID commands.
"""

import logging
import math
import os
import threading
import time


logger = logging.getLogger(__name__)

DEFAULT_PWM_CHIP = "/sys/class/pwm/pwmchip0"
# Pi 5 RP1 PWM0 alternate functions used by the two-axis mount:
# GPIO12 -> PWM0_CHAN0, GPIO13 -> PWM0_CHAN1.
DEFAULT_GPIO_TO_PWM = {12: 0, 13: 1}


class ServoController:
    """Control position-type FS90 servos through Pi 5 hardware PWM."""

    def __init__(self, config: dict):
        pwm_config = config.get("pwm", {})
        self.pwm_chip = str(
            pwm_config.get("chip", DEFAULT_PWM_CHIP)
        )
        channel_map = dict(DEFAULT_GPIO_TO_PWM)
        channel_map.update({
            int(gpio): int(channel)
            for gpio, channel in pwm_config.get("gpio_to_channel", {}).items()
        })

        self.servos_data = config["servo"]
        self._servos = {}
        self._owned_channels = set()
        self._lock = threading.RLock()
        self._closed = False
        self._process_lock = None

        default_lock_file = (
            "/tmp/pi5_ai_camera_pwm.lock" if os.name == "posix" else ""
        )
        lock_file = pwm_config.get("lock_file", default_lock_file)
        if lock_file:
            self._acquire_process_lock(str(lock_file))

        try:
            for name, raw_cfg in self.servos_data.items():
                servo = self._parse_servo_config(name, raw_cfg, channel_map)
                if any(
                    existing["channel"] == servo["channel"]
                    for existing in self._servos.values()
                ):
                    raise ValueError(
                        f"PWM channel {servo['channel']} is assigned more than once"
                    )
                # Register before touching sysfs so cleanup can recover from a
                # partial export/configuration failure.
                self._servos[name] = servo
                initial_pulse = self._angle_to_pulse(
                    servo, servo["center_angle"]
                )
                self._init_pwm(
                    servo["channel"], servo["period_ns"], initial_pulse
                )
                servo["angle"] = servo["center_angle"]
                logger.info(
                    "%s: GPIO%s -> pwm%s, %.1f..%.1f deg",
                    name,
                    servo["pin"],
                    servo["channel"],
                    servo["min_angle"],
                    servo["max_angle"],
                )
        except Exception:
            self.cleanup()
            raise

    @staticmethod
    def _parse_servo_config(name, cfg, channel_map):
        try:
            pin = int(cfg["pin"])
            frequency = float(cfg.get("freq", cfg.get("frequency_hz", 50)))
            min_pulse_us = float(cfg.get("min_pulse", 900))
            max_pulse_us = float(cfg.get("max_pulse", 2100))
            pulse_min_angle = float(cfg.get("pulse_min_angle", 0))
            pulse_max_angle = float(cfg.get("pulse_max_angle", 180))
            min_angle = float(cfg.get("min_angle", 20))
            max_angle = float(cfg.get("max_angle", 160))
            center_angle = float(cfg.get("center_angle", 90))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid configuration for {name}") from exc

        values = (
            frequency,
            min_pulse_us,
            max_pulse_us,
            pulse_min_angle,
            pulse_max_angle,
            min_angle,
            max_angle,
            center_angle,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"non-finite configuration value for {name}")
        if pin not in channel_map:
            raise ValueError(f"No PWM channel for GPIO{pin}")
        if frequency <= 0:
            raise ValueError(f"{name}.freq must be positive")
        if min_pulse_us <= 0 or min_pulse_us >= max_pulse_us:
            raise ValueError(f"invalid pulse range for {name}")
        if pulse_min_angle >= pulse_max_angle:
            raise ValueError(f"invalid pulse calibration angles for {name}")
        if min_angle >= max_angle:
            raise ValueError(f"invalid angle range for {name}")
        if not (
            pulse_min_angle <= min_angle <= center_angle
            <= max_angle <= pulse_max_angle
        ):
            raise ValueError(
                f"{name} allowed angles must lie inside its pulse calibration"
            )

        period_ns = int(round(1_000_000_000 / frequency))
        min_pulse_ns = int(round(min_pulse_us * 1000))
        max_pulse_ns = int(round(max_pulse_us * 1000))
        if max_pulse_ns >= period_ns:
            raise ValueError(f"{name}.max_pulse must be shorter than one period")

        return {
            "pin": pin,
            "channel": channel_map[pin],
            "period_ns": period_ns,
            "min_pulse_ns": min_pulse_ns,
            "max_pulse_ns": max_pulse_ns,
            "pulse_min_angle": pulse_min_angle,
            "pulse_max_angle": pulse_max_angle,
            "min_angle": min_angle,
            "max_angle": max_angle,
            "center_angle": center_angle,
        }

    @staticmethod
    def _angle_to_pulse(servo, angle):
        fraction = (
            (angle - servo["pulse_min_angle"])
            / (servo["pulse_max_angle"] - servo["pulse_min_angle"])
        )
        return int(round(
            servo["min_pulse_ns"]
            + fraction * (servo["max_pulse_ns"] - servo["min_pulse_ns"])
        ))

    def _attribute_path(self, channel, attribute):
        return os.path.join(self.pwm_chip, f"pwm{channel}", attribute)

    @staticmethod
    def _write(path, value):
        with open(path, "w", encoding="ascii") as handle:
            handle.write(str(value))

    def _acquire_process_lock(self, lock_file):
        """Prevent two GPIO service instances from driving the same PWM."""
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Raspberry Pi uses POSIX fcntl
            logger.warning("process-level PWM lock unavailable on this platform")
            return

        handle = open(lock_file, "a+", encoding="ascii")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            handle.close()
            raise RuntimeError(
                "another GPIO service already owns the PWM outputs"
            ) from exc
        self._process_lock = handle

    def _init_pwm(self, channel, period_ns, initial_pulse_ns):
        channel_path = os.path.join(self.pwm_chip, f"pwm{channel}")
        if not os.path.isdir(channel_path):
            self._write(os.path.join(self.pwm_chip, "export"), channel)
            deadline = time.monotonic() + 0.5
            while not os.path.isdir(channel_path) and time.monotonic() < deadline:
                time.sleep(0.01)
            if not os.path.isdir(channel_path):
                raise RuntimeError(f"pwm{channel} did not appear after export")
            self._owned_channels.add(channel)

        # A channel left enabled by an interrupted previous process rejects
        # period changes on many kernels.  Reconfigure it in the safe order.
        self._write(self._attribute_path(channel, "enable"), 0)
        self._write(self._attribute_path(channel, "duty_cycle"), 0)
        self._write(self._attribute_path(channel, "period"), period_ns)
        polarity_path = self._attribute_path(channel, "polarity")
        if os.path.exists(polarity_path):
            self._write(polarity_path, "normal")
        self._write(
            self._attribute_path(channel, "duty_cycle"), initial_pulse_ns
        )
        self._write(self._attribute_path(channel, "enable"), 1)

    @staticmethod
    def _coerce_angle(angle):
        if isinstance(angle, bool):
            raise ValueError("servo angle must be a finite number")
        try:
            angle = float(angle)
        except (TypeError, ValueError) as exc:
            raise ValueError("servo angle must be a finite number") from exc
        if not math.isfinite(angle):
            raise ValueError("servo angle must be a finite number")
        return angle

    def _prepare_angle(self, servo_num, angle):
        try:
            servo_num = int(servo_num)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid servo number: {servo_num}") from exc
        servo_key = f"servo{servo_num}"
        servo = self._servos.get(servo_key)
        if servo is None:
            raise ValueError(f"Invalid servo number: {servo_num}")
        angle = self._coerce_angle(angle)
        angle = max(servo["min_angle"], min(servo["max_angle"], angle))
        return servo_key, servo, angle, self._angle_to_pulse(servo, angle)

    def set_angle(self, servo_num: int, angle: float) -> float:
        """Set one servo and return the clamped angle that was applied."""
        with self._lock:
            if self._closed:
                raise RuntimeError("servo controller is closed")
            servo_key, servo, angle, pulse = self._prepare_angle(
                servo_num, angle
            )
            self._write(
                self._attribute_path(servo["channel"], "duty_cycle"), pulse
            )
            servo["angle"] = angle
            logger.debug("%s -> %.1f deg", servo_key, angle)
            return angle

    def set_angles(self, angles: dict) -> dict:
        """Apply one paired gimbal command in-order under a single lock."""
        if not isinstance(angles, dict) or not angles:
            raise ValueError("gimbal angles must be a non-empty object")
        with self._lock:
            if self._closed:
                raise RuntimeError("servo controller is closed")
            prepared = [
                self._prepare_angle(servo_num, angle)
                for servo_num, angle in angles.items()
            ]
            applied = {}
            for servo_key, servo, angle, pulse in prepared:
                self._write(
                    self._attribute_path(servo["channel"], "duty_cycle"),
                    pulse,
                )
                servo["angle"] = angle
                applied[servo_key] = angle
            logger.debug("gimbal -> %s", applied)
            return applied

    def release(self, servo_num: int) -> None:
        """Return one servo to its configured neutral position."""
        servo_key = f"servo{int(servo_num)}"
        servo = self._servos.get(servo_key)
        if servo is not None:
            self.set_angle(servo_num, servo["center_angle"])

    def cleanup(self) -> None:
        """Disable PWM outputs and unexport only channels opened by this process."""
        with self._lock:
            if self._closed:
                return
            for name, servo in self._servos.items():
                channel = servo["channel"]
                try:
                    self._write(self._attribute_path(channel, "enable"), 0)
                    if channel in self._owned_channels:
                        self._write(
                            os.path.join(self.pwm_chip, "unexport"), channel
                        )
                    logger.info("released %s (pwm%s)", name, channel)
                except OSError as exc:
                    logger.warning("cleanup %s failed: %s", name, exc)
            if self._process_lock is not None:
                try:
                    import fcntl
                    fcntl.flock(self._process_lock.fileno(), fcntl.LOCK_UN)
                finally:
                    self._process_lock.close()
                    self._process_lock = None
            self._closed = True
