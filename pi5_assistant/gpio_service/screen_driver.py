"""Screen driver — abstracted display output for OLED / TFT / HDMI / LCD.

Currently supports SSD1306 I2C OLED (128×64).
Extend this class for other screen types.
"""

import logging

logger = logging.getLogger(__name__)


class ScreenDriver:
    """Abstract screen driver with pluggable backends."""

    def __init__(self, config: dict):
        self.cfg = config["screen"]
        self._device = None
        self._init_display()

    def _init_display(self) -> None:
        """Initialize the display hardware."""
        screen_type = self.cfg.get("type", "oled")
        if screen_type == "oled":
            self._init_oled()
        elif screen_type == "tft":
            self._init_tft()
        elif screen_type == "hdmi":
            self._init_hdmi()
        elif screen_type == "lcd":
            self._init_lcd()
        else:
            logger.warning(f"Unknown screen type '{screen_type}', using dummy")
            self._init_dummy()

    def _init_oled(self) -> None:
        """Initialize SSD1306 OLED display via I2C."""
        try:
            import board
            import busio
            import adafruit_ssd1306

            i2c = busio.I2C(board.SCL, board.SDA)
            self._device = adafruit_ssd1306.SSD1306_I2C(
                self.cfg["width"],
                self.cfg["height"],
                i2c,
                addr=self.cfg.get("i2c_addr", 0x3C),
            )
            logger.info("OLED initialized")
        except (ImportError, ValueError, OSError) as e:
            logger.warning(f"OLED init failed ({e}), using dummy display")
            self._init_dummy()

    def _init_tft(self) -> None:
        """Initialize TFT display (placeholder)."""
        logger.info("TFT display support not yet implemented")
        self._init_dummy()

    def _init_hdmi(self) -> None:
        """Initialize HDMI display (placeholder — uses framebuffer)."""
        logger.info("HDMI display support not yet implemented")
        self._init_dummy()

    def _init_lcd(self) -> None:
        """Initialize character LCD via I2C (placeholder)."""
        logger.info("Character LCD support not yet implemented")
        self._init_dummy()

    def _init_dummy(self) -> None:
        """Dummy display for development/testing."""
        self._device = None
        logger.info("Using dummy display (no hardware)")

    def display(self, text: str, clear: bool = True) -> None:
        """Display text on the screen."""
        if clear and self._device is not None:
            self._device.fill(0)

        if self._device is not None:
            self._device.text(text, 0, 0, 1)
            self._device.show()
        else:
            logger.info(f"[SCREEN] {text}")

    def clear(self) -> None:
        """Clear the display."""
        if self._device is not None:
            self._device.fill(0)
            self._device.show()
        else:
            logger.info("[SCREEN] (cleared)")

    def cleanup(self) -> None:
        """Power off and clean up display."""
        if self._device is not None:
            self._device.fill(0)
            self._device.show()
