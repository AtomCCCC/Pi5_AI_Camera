"""Text-to-speech engine using Piper.

Piper is the fastest CPU-based TTS for Raspberry Pi.
~200ms to generate a short response.
"""

import subprocess
import tempfile
import wave
import numpy as np
import sounddevice as sd


class TTSEngine:
    """Text-to-speech via Piper TTS."""

    def __init__(self, voice: str = "en_US-amy-medium",
                 speed: float = 1.0,
                 volume: float = 1.0):
        self.voice = voice
        self.speed = speed
        self.volume = volume
        self._process = None

    def speak(self, text: str, blocking: bool = True):
        """Synthesize and play text.
        
        Args:
            text: Text to speak
            blocking: If True, waits for playback to finish
        """
        audio = self.synthesize(text)
        if blocking:
            sd.play(audio, samplerate=22050)
            sd.wait()
        else:
            sd.play(audio, samplerate=22050)

    def synthesize(self, text: str) -> np.ndarray:
        """Synthesize text to audio array without playing."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            out_path = tmp.name

        subprocess.run(
            ["piper", "--model", self.voice, "--output_file", out_path],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=30
        )

        with wave.open(out_path, "rb") as wf:
            audio = np.frombuffer(wf.readframes(wf.getnframes()),
                                  dtype=np.int16).astype(np.float32) / 32767.0

        return audio

    def speak_async(self, text: str, on_done=None):
        """Speak in a non-blocking way.
        
        Args:
            text: Text to speak
            on_done: Optional callback when playback finishes
        """
        audio = self.synthesize(text)
        sd.play(audio, samplerate=22050)

        if on_done:
            import threading
            def _wait():
                sd.wait()
                on_done()
            threading.Thread(target=_wait, daemon=True).start()

    def stop(self):
        """Stop current playback immediately."""
        sd.stop()
