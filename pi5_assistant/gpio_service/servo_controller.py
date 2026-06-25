"""Servo motor controller using hardware PWM (pigpio).

BCM GPIO 12 and 13 support hardware PWM via pigpio library.
Servo angle is converted to pulse width using linear interpolation.
"""

import logging
from gpio_service.pin_config import PinConfig

logger = logging.getLogger(__name__)


class ServoController:
    """Controls 2 servos via hardware PWM (pigpio daemon)."""

    def __init__(self, config: dict):
        self.servos = config["servo"]
        self.pins = PinConfig()

        for name, cfg in self.servos.items():
            pin = cfg["pin"]
            freq = cfg.get("freq", 50)
            self.pins.set_pwm(pin, freq)
            logger.info(f"  {name}: pin={pin}, freq={freq}Hz")

    def set_angle(self, servo_num: int, angle: int) -> None:
        """Set servo to angle (0-180°) using hardware PWM."""
        servo_key = f"servo{servo_num}"
        cfg = self.servos.get(servo_key)
        if not cfg:
            raise ValueError(f"Invalid servo number: {servo_num}")

        # Clamp angle
        angle = max(0, min(180, angle))

        # Linear interpolation: min_pulse..max_pulse over 0..180°
        pulse_range = cfg["max_pulse"] - cfg["min_pulse"]
        pulse_us = cfg["min_pulse"] + (pulse_range * angle // 180)

        pin = cfg["pin"]
        freq = cfg.get("freq", 50)

        # pigpio expects duty cycle in microseconds
        self.pins.set_servo_pulsewidth(pin, pulse_us)
        logger.info(f"  Servo {servo_num} → {angle}° ({pulse_us}µs)")

    def release(self, servo_num: int) -> None:
        """Release PWM signal from servo."""
        servo_key = f"servo{servo_num}"
        cfg = self.servos.get(servo_key)
        if not cfg:
            raise ValueError(f"Invalid servo number: {servo_num}")
        self.pins.set_servo_pulsewidth(cfg["pin"], 0)

    def cleanup(self) -> None:
        """Release all servos and stop pigpio."""
        for name, cfg in self.servos.items():
            self.pins.set_servo_pulsewidth(cfg["pin"], 0)
            logger.info(f"  Released {name} (pin {cfg['pin']})")
        self.pins.stop()
