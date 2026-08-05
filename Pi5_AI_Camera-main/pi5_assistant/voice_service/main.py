"""Voice Service — main entry point.

Wakes on wake word, captures audio, transcribes via STT, publishes
to command/in, subscribes to response/out for TTS playback.
Handles voice interrupt (new wake word during TTS stops playback).
"""

import sys
import os
import yaml
import uuid
import threading
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pi5_assistant.mqtt_client import MQTTClient
from voice_service.wake_detector import WakeDetector
from voice_service.stt_engine import STTEngine
from voice_service.tts_engine import TTSEngine


class VoiceService:
    """Orchestrates wake → STT → MQTT → TTS."""

    def __init__(self, config_path: str | None = None):
        config_path = config_path or Path(__file__).with_name("config.yaml")
        with open(config_path) as f:
            cfg = yaml.safe_load(f)

        self.mqtt = MQTTClient("voice", cfg["mqtt"]["broker"], cfg["mqtt"]["port"])
        self.wake = WakeDetector(
            model_name=cfg["wake_word"]["model"],
            sensitivity=cfg["wake_word"]["sensitivity"],
            audio_buffer_seconds=cfg["wake_word"]["audio_buffer_seconds"],
        )
        self.stt = STTEngine(
            engine=cfg["stt"]["engine"],
            model_size=cfg["stt"]["model_size"],
            language=cfg["stt"]["language"],
            compute_type=cfg["stt"]["compute_type"],
        )
        self.tts = TTSEngine(
            voice=cfg["tts"]["voice"],
            speed=cfg["tts"]["speed"],
            volume=cfg["tts"]["volume"],
        )

        self.topic_command_in = cfg["mqtt"]["topic_command_in"]
        self.topic_response_out = cfg["mqtt"]["topic_response_out"]
        self.topic_interrupt = cfg["mqtt"]["topic_interrupt"]

        self._interrupt_flag = False

    def run(self):
        """Start the voice service."""

        # Subscribe to response/out for TTS
        self.mqtt.subscribe(self.topic_response_out, self._on_response)

        # Subscribe to interrupt topic
        self.mqtt.subscribe(self.topic_interrupt, self._on_interrupt)

        # Start wake word listener
        self.wake.start(on_wake=self._on_wake)

        print("[Voice] Service started. Waiting for wake word...")
        try:
            threading.Event().wait()  # Sleep forever
        except KeyboardInterrupt:
            self.stop()

    def _on_wake(self, audio_buffer):
        """Called when wake word is detected."""
        # Set interrupt flag to stop any current TTS
        self._interrupt_flag = True
        self.tts.stop()

        # Transcribe
        text = self.stt.transcribe(audio_buffer)
        if not text.strip():
            self._interrupt_flag = False
            return

        session_id = str(uuid.uuid4())
        print(f"[Voice] [{session_id}] → {text}")

        # Publish command
        self.mqtt.publish(self.topic_command_in, {
            "text": text,
            "session_id": session_id,
        })
        self._interrupt_flag = False

    def _on_response(self, payload):
        """Called when a response is received from the LLM."""
        text = payload.get("text", "")
        if not text:
            return
        print(f"[Voice] ← {text[:80]}...")
        # Speak each sentence, checking interrupt flag
        for sentence in text.replace("? ", "?\n").replace(". ", ".\n").split("\n"):
            sentence = sentence.strip()
            if not sentence:
                continue
            if self._interrupt_flag:
                break
            self.tts.speak(sentence, blocking=True)

    def _on_interrupt(self, payload):
        """Called when an interrupt signal is received."""
        self._interrupt_flag = True
        self.tts.stop()

    def stop(self):
        self.wake.stop()
        self.mqtt.stop()


if __name__ == "__main__":
    service = VoiceService()
    service.run()
