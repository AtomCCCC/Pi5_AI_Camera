"""Speech-to-text engine.

Primary: faster-whisper (runs whisper.cpp under the hood, much faster than
         original Whisper).
Fallback: Vosk (lighter, entirely offline).
"""

import numpy as np


class STTEngine:
    """Speech-to-text abstraction."""

    def __init__(self, engine: str = "faster-whisper",
                 model_size: str = "tiny",
                 language: str = "en",
                 compute_type: str = "int8"):
        self.engine_name = engine
        self.model_size = model_size
        self.language = language
        self.compute_type = compute_type
        self._model = None

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """Transcribe audio to text."""
        if self.engine_name == "faster-whisper":
            return self._transcribe_faster_whisper(audio, sample_rate)
        elif self.engine_name == "vosk":
            return self._transcribe_vosk(audio, sample_rate)
        raise ValueError(f"Unknown STT engine: {self.engine_name}")

    def _transcribe_faster_whisper(self, audio: np.ndarray,
                                    sample_rate: int) -> str:
        from faster_whisper import WhisperModel
        if self._model is None:
            self._model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type=self.compute_type,
                cpu_threads=4,
                num_workers=2
            )
        segments, _info = self._model.transcribe(audio, language=self.language)
        return " ".join(seg.text for seg in segments)

    def _transcribe_vosk(self, audio: np.ndarray, sample_rate: int) -> str:
        import vosk
        import json
        if self._model is None:
            # Expects model in ~/.local/share/vosk/
            vosk.SetLogLevel(-1)
            self._model = vosk.Model(lang=self.language)
        rec = vosk.KaldiRecognizer(self._model, sample_rate)
        rec.AcceptWaveform((audio * 32767).astype("int16").tobytes())
        result = json.loads(rec.FinalResult())
        return result.get("text", "")
