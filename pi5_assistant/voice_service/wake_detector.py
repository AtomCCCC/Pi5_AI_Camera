"""Wake word detection using OpenWakeWord.

Runs continuously in a background thread at 3-8% CPU idle.
Maintains a circular audio buffer so preceding speech is not lost.
"""

import threading
import numpy as np
import sounddevice as sd


class WakeDetector:
    """Always-on wake word listener."""

    def __init__(self, model_name: str = "hey_raspberry",
                 sensitivity: float = 0.5,
                 sample_rate: int = 16000,
                 audio_buffer_seconds: int = 5):
        self.model_name = model_name
        self.sensitivity = sensitivity
        self.sample_rate = sample_rate
        self.buffer_seconds = audio_buffer_seconds

        self._engine = None
        self._running = False
        self._thread: threading.Thread | None = None

        # Circular buffer — keeps audio before wake word
        self.buffer_size = sample_rate * audio_buffer_seconds
        self._audio_buffer = np.zeros(self.buffer_size, dtype=np.float32)
        self._buffer_pos = 0

    def start(self, on_wake):
        """Start the wake word listener.
        
        Args:
            on_wake: Callback invoked as on_wake(audio_buffer: np.ndarray)
        """
        from openwakeword import Model
        self._engine = Model(wakeword_models=[self.model_name])
        self._running = True
        self._thread = threading.Thread(
            target=self._listen_loop,
            args=(on_wake,),
            daemon=True
        )
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _listen_loop(self, on_wake):
        def audio_callback(indata, _frames, _time, _status):
            # Flatten to mono float32
            audio = np.squeeze(indata).astype(np.float32)

            # Feed into circular buffer
            n = len(audio)
            if self._buffer_pos + n <= self.buffer_size:
                self._audio_buffer[self._buffer_pos:self._buffer_pos + n] = audio
            else:
                # Wrap around
                first = self.buffer_size - self._buffer_pos
                self._audio_buffer[self._buffer_pos:] = audio[:first]
                self._audio_buffer[:n - first] = audio[first:]
            self._buffer_pos = (self._buffer_pos + n) % self.buffer_size

            # Run wake word detection
            prediction = self._engine.predict(audio)
            if prediction[self.model_name] >= self.sensitivity:
                # Grab the full buffer as context
                if self._buffer_pos == 0:
                    context = self._audio_buffer.copy()
                else:
                    context = np.concatenate([
                        self._audio_buffer[self._buffer_pos:],
                        self._audio_buffer[:self._buffer_pos]
                    ])
                on_wake(context)

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            callback=audio_callback,
            blocksize=8000  # ~500ms chunks
        ):
            while self._running:
                sd.sleep(100)

    @property
    def is_running(self) -> bool:
        return self._running
