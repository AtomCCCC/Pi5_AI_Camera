"""GPIO pin configuration for Pi 5.

Uses gpiozero with lgpio backend for GPIO and hardware PWM on Pi 5.
"""

import logging

logger = logging.getLogger(__name__)


class PinConfig:
    """gpiozero-based GPIO control for Pi 5."""

    def __init__(self):
        self._ensure_lgpio()
        from gpiozero import Device
        from gpiozero.pins.lgpio import LGPIOFactory
        try:
            Device.pin_factory = LGPIOFactory()
            logger.info("GPIO: using lgpio pin factory")
        except Exception as e:
            logger.info(f"lgpio unavailable ({e}), using default pin factory")

    def _ensure_lgpio(self) -> None:
        """Verify lgpio is available."""
        try:
            import lgpio
            logger.info(f"lgpio available: v{lgpio.get_module_version()}")
        except ImportError:
            logger.warning("lgpio not available, falling back to default")

    def write_pin(self, pin: int, value: bool) -> None:
        """Set a digital GPIO pin HIGH (True) or LOW (False)."""
        from gpiozero import DigitalOutputDevice
        dev = DigitalOutputDevice(pin, active_high=True)
        if value:
            dev.on()
        else:
            dev.off()
        dev.close()

    def read_pin(self, pin: int) -> bool:
        """Read the current value of a digital GPIO pin."""
        from gpiozero import DigitalInputDevice
        dev = DigitalInputDevice(pin)
        val = dev.value
        dev.close()
        return bool(val)

    def stop(self) -> None:
        """Cleanup resources."""
        pass
