"""Session Manager — main entry point.

Manages session lifecycle:
  - Creates session on wake word detection
  - Forwards user text to LLM Orchestrator (command/in)
  - Handles voice interrupts (new wake word → kill TTS → new session)
  - Auto-ends sessions on timeout
"""

import sys
import os
import yaml
import json
import logging
import uuid
import threading
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pi5_assistant.mqtt_client import MQTTClient
from session_manager.conversation_store import ConversationStore
from session_manager.interrupt_handler import InterruptHandler

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages conversation sessions and interrupt lifecycle."""

    def __init__(self, config_path: str | None = None):
        config_path = config_path or Path(__file__).with_name("config.yaml")
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

        self.mqtt = MQTTClient("session", self.cfg["mqtt"]["broker"],
                               self.cfg["mqtt"]["port"])
        self.store = ConversationStore(
            max_history=self.cfg["session"]["max_history"],
            timeout=self.cfg["session"]["timeout"],
        )
        self.interrupt = InterruptHandler(self.mqtt, self.cfg)

        self._current_session: str | None = None

    def run(self):
        self.mqtt.subscribe(self.cfg["mqtt"]["topic_session_create"], self._on_session_create)
        self.mqtt.subscribe(self.cfg["mqtt"]["topic_interrupt"], self._on_interrupt)

        # Forward user transcript from voice service to LLM (via command/in)
        self.mqtt.subscribe("voice/transcript", self._on_user_text)

        print("[SESSION] Manager started. Listening for sessions and interrupts...")

        # Periodic timeout cleanup
        def timeout_check():
            while True:
                self.store._cleanup()
                threading.Event().wait(15)
        threading.Thread(target=timeout_check, daemon=True).start()

        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            self.stop()

    def _on_session_create(self, payload):
        """Create a new conversation session."""
        session_id = payload.get("session_id", str(uuid.uuid4()))
        self.store.create_session(session_id)
        self._current_session = session_id
        logger.info(f"Session created: {session_id}")

    def _on_interrupt(self, payload):
        """Handle a voice interrupt from wake word detection."""
        new_id = self.interrupt.handle_interrupt(payload)
        self.store.create_session(new_id)
        self._current_session = new_id

    def _on_user_text(self, payload):
        """Forward user voice transcript to LLM orchestrator."""
        text = payload.get("text", "")
        if not text.strip():
            return

        # Use existing session or create one
        session_id = self._current_session or str(uuid.uuid4())
        if not self._current_session:
            self.store.create_session(session_id)
            self._current_session = session_id

        # Store user message
        self.store.add_user_message(session_id, text)

        # Forward to LLM orchestrator
        self.mqtt.publish(self.cfg["mqtt"]["topic_command_out"], {
            "text": text,
            "session_id": session_id,
        })
        logger.info(f"[SESSION] Forwarded: {text[:60]}... ({session_id})")

    def stop(self):
        self.mqtt.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mgr = SessionManager()
    mgr.run()
