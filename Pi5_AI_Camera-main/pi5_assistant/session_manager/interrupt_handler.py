"""Voice interrupt handler.

When a new wake word is detected while a session is active:
1. TTS is stopped (via MQTT interrupt topic)
2. The current session is ended
3. A new session is created for the next voice input
"""

import logging
import uuid

logger = logging.getLogger(__name__)


class InterruptHandler:
    """Handles voice interrupt events between sessions."""

    def __init__(self, mqtt, config: dict):
        self.mqtt = mqtt
        self.cfg = config
        self._current_session: str | None = None

    @property
    def current_session(self) -> str | None:
        return self._current_session

    def handle_interrupt(self, payload: dict) -> str:
        """Process an interrupt event.
        
        Returns the new session ID.
        """
        # Stop TTS
        self.mqtt.publish("tts/stop", {})

        # End current session
        if self._current_session:
            self.mqtt.publish("session/end", {
                "session_id": self._current_session,
                "reason": "interrupt",
            })

        # Create new session
        new_session_id = str(uuid.uuid4())
        self._current_session = new_session_id

        self.mqtt.publish("session/create", {
            "session_id": new_session_id,
            "reason": "new_wake_word",
        })

        logger.info(f"Interrupt: {self._current_session} → {new_session_id}")
        return new_session_id
