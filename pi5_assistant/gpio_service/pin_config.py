"""GPIO pin configuration and pigpio interface.

Manages pigpio connection for hardware PWM and digital I/O.
"""

import logging
import subprocess

logger = logging.getLogger(__name__)


class PinConfig:
    """pigpio-based GPIO control for servos and digital pins."""

    def __init__(self):
        self._ensure_pigpiod()
        import pigpio
        self.pi = pigpio.pi()
        if not self.pi.connected:
            raise RuntimeError("Could not connect to pigpio daemon")

    def _ensure_pigpiod(self) -> None:
        """Start pigpiod if it's not already running."""
        try:
            result = subprocess.run(
                ["pgrep", "pigpiod"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode != 0:
                subprocess.run(
                    ["sudo", "pigpiod"],
                    check=False,
                    timeout=10,
                )
                logger.info("Started pigpiod daemon")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning("Could not start/check pigpiod")

    def set_pwm(self, pin: int, freq: int) -> None:
        """Configure a pin for hardware PWM."""
        self.pi.set_mode(pin, pigpio.ALT0)
        self.pi.set_PWM_frequency(pin, freq)
        logger.debug(f"  PWM configured: pin={pin}, freq={freq}Hz")

    def set_servo_pulsewidth(self, pin: int, pulsewidth: int) -> None:
        """Set servo pulse width in microseconds (0 disables PWM)."""
        self.pi.set_servo_pulsewidth(pin, pulsewidth)

    def write_pin(self, pin: int, value: bool) -> None:
        """Set a digital GPIO pin HIGH (True) or LOW (False)."""
        self.pi.write(pin, 1 if value else 0)

    def read_pin(self, pin: int) -> bool:
        """Read the current value of a digital GPIO pin."""
        return bool(self.pi.read(pin))

    def stop(self) -> None:
        """Disconnect from pigpio."""
        if hasattr(self, 'pi'):
            self.pi.stop()
            logger.info("pigpio connection closed")
