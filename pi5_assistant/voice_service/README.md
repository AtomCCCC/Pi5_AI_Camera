# Voice Service

Handles voice I/O: wake word detection, speech-to-text, and text-to-speech.

## Files

| File | Role |
|------|------|
| `main.py` | Entry point, subscribes to `response/out` and `tts/stop`, publishes `voice/transcript` |
| `wake_detector.py` | Porcupine or custom wake word engine — detects keyword and triggers session |
| `stt_engine.py` | Whisper.cpp — converts mic audio to text after wake word |
| `tts_engine.py` | Piper TTS (local) or ElevenLabs API — speaks LLM responses |
| `config.yaml` | Model paths, mic/speaker device, wake word sensitivity |

## MQTT

| Direction | Topic | Payload |
|-----------|-------|---------|
| Publish | `voice/transcript` | `{text, session_id}` |
| Publish | `interrupt` | `{}` (on new wake word) |
| Subscribe | `response/out` | `{text, session_id}` → speaks it |
| Subscribe | `tts/stop` | `{}` → stops current playback |

## Extending

- **Replace STT**: implement `STTEngine` interface with `transcribe(audio) → str`
- **Replace TTS**: implement `TTSEngine` interface with `speak(text) → None`
- **Add wake word**: add model file to `wake_detector.py` config
