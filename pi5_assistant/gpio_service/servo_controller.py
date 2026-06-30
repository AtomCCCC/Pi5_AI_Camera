"""Servo motor controller for Pi 5 — hardware PWM via kernel sysfs.

Uses /sys/class/pwm/pwmchip0 for glitch-free hardware PWM on Pi 5.
GPIO mapping: 18→pwm2, 12→pwm0
"""

import time
import os
import logging

logger = logging.getLogger(__name__)

PWM_CHIP = "/sys/class/pwm/pwmchip0"
PWM_PERIOD_NS = 20_000_000   # 20ms → 50Hz
MIN_PULSE_NS  = 500_000      # 500μs → 0°
MAX_PULSE_NS  = 2_500_000    # 2500μs → 180°

GPIO_TO_PWM = {18: 2, 12: 0}


class ServoController:
    """Controls 2 servos via Pi 5 hardware PWM."""

    def __init__(self, config: dict):
        self.servos_data = config["servo"]
        self._servos = {}

        for name, cfg in self.servos_data.items():
            gpio = cfg["pin"]
            pwm_ch = GPIO_TO_PWM.get(gpio)
            if pwm_ch is None:
                raise ValueError(f"No PWM channel for GPIO{gpio}")
            self._init_pwm(pwm_ch)
            self._set_angle_pwm(pwm_ch, 90)
            self._servos[name] = pwm_ch
            logger.info(f"  {name}: GPIO{gpio} → pwm{pwm_ch} (hw)")

    def _init_pwm(self, channel: int):
        path = f"{PWM_CHIP}/pwm{channel}"
        if not os.path.exists(path):
            with open(f"{PWM_CHIP}/export", "w") as f:
                f.write(str(channel))
            time.sleep(0.1)

        with open(f"{path}/period", "w") as f:
            f.write(str(PWM_PERIOD_NS))
        with open(f"{path}/enable", "w") as f:
            f.write("1")

    def _set_angle_pwm(self, channel: int, angle: int):
        angle = max(0, min(180, angle))
        pulse = int(MIN_PULSE_NS + (angle / 180.0) * (MAX_PULSE_NS - MIN_PULSE_NS))
        with open(f"{PWM_CHIP}/pwm{channel}/duty_cycle", "w") as f:
            f.write(str(pulse))

    def set_angle(self, servo_num: int, angle: int) -> None:
        servo_key = f"servo{servo_num}"
        pwm_ch = self._servos.get(servo_key)
        if pwm_ch is None:
            raise ValueError(f"Invalid servo number: {servo_num}")
        self._set_angle_pwm(pwm_ch, angle)
        logger.info(f"  Servo {servo_num} → {angle}°")

    def release(self, servo_num: int) -> None:
        servo_key = f"servo{servo_num}"
        pwm_ch = self._servos.get(servo_key)
        if pwm_ch is not None:
            self._set_angle_pwm(pwm_ch, 90)

    def cleanup(self) -> None:
        for name, pwm_ch in self._servos.items():
            try:
                with open(f"{PWM_CHIP}/pwm{pwm_ch}/enable", "w") as f:
                    f.write("0")
                with open(f"{PWM_CHIP}/unexport", "w") as f:
                    f.write(str(pwm_ch))
                logger.info(f"  Released {name} (pwm{pwm_ch})")
            except Exception as e:
                logger.warning(f"  Cleanup {name} error: {e}")
