# Voice Service

Handles voice I/O: wake word detection, speech-to-text, and text-to-speech. This is the user's primary interface — everything starts with a wake word and ends with a spoken response.

## Files

| File | Role |
|------|------|
| `main.py` | Entry point, subscribes to `response/out` and `tts/stop` / `session/interrupt`, publishes `command/in` |
| `wake_detector.py` | OpenWakeWord — always-on, 3-8% CPU idle, circular audio buffer |
| `stt_engine.py` | faster-whisper tiny (default) / Vosk (offline fallback) |
| `tts_engine.py` | Piper TTS (local, ~200ms per response) |
| `config.yaml` | Model paths, mic/speaker device, wake word sensitivity |

## Processing Flow

```
┌──────────────────────────────────────────────────────────────────┐
│                      VOICE SERVICE METHODOLOGY                    │
│                                                                   │
│  ┌──────────────┐    ┌──────────────────┐    ┌─────────────────┐  │
│  │ WAKE WORD    │    │ STT (faster-   │    │ TTS (Piper)    │  │
│  │ Detection    │───►│ whisper / Vosk)│───►│                 │  │
│  │              │    │ Transcription  │    │ Audio playback  │  │
│  │ OpenWakeWord │    │ faster-whisper │    │ Piper TTS       │  │
│  │ continuously │    │ tiny on CPU    │    │ (local, fast)   │  │
│  │ listens on   │    │ or Vosk fallbk │    │                 │  │
│  │ microphone   │    │ converts audio │    │ synthesizes     │  │
│  │              │    │ buffer to text │    │ + plays audio   │  │
│  └──────┬───────┘    └────────┬─────────┘    └────────┬─────────┘  │
│         │                    │                        │           │
│         │  publish:          │  publish:              │  speak    │
│         │  "interrupt"       │  "voice/transcript"    │  audio    │
│         │  (kill current     │  {text, session_id}    │  out of   │
│         │   session + TTS)   │  ──► Session Manager   │  speaker  │
│         │                    │                        │           │
│  ┌──────┴────────────────────┴────────────────────────┴──────────┐ │
│  │                    MQTT PUBLISH / SUBSCRIBE                    │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  Subscribed topics:                Published topics:              │
│  • response/out ← LLM response     • voice/transcript → Session   │
│  • tts/stop      ← interrupt kill  • interrupt → Session Manager │
└───────────────────────────────────────────────────────────────────┘
```

### Step-by-Step

1. **Idle** — `wake_detector.py` continuously listens on the microphone for the wake word (e.g. "Hey Raspberry") via OpenWakeWord, maintaining a 5-second circular audio buffer.
2. **Wake Word Detected** — If TTS is currently playing, the service sets an interrupt flag and stops playback immediately. It then transcribes the buffered audio.
3. **STT** — `stt_engine.py` sends the audio buffer to faster-whisper (tiny model, int8), which returns transcribed text.
4. **Publish** — Text is published as `command/in` for the LLM Orchestrator to process.
5. **Wait for Response** — The service subscribes to `response/out`, waiting for the LLM's final answer.
6. **TTS** — `tts_engine.py` converts the response text to speech via Piper TTS (local, ~200ms). Each sentence is synthesized and played, checking for interrupt between sentences.
7. **Speak** — Audio plays out of the speaker. If interrupted by a new wake word, go to step 2.

## MQTT

| Direction | Topic | Payload | When |
|-----------|-------|---------|------|
| Publish | `command/in` | `{text, session_id}` | → LLM Orchestrator (after STT) |
| Subscribe | `response/out` | `{text, session_id}` | → speaks the response via TTS |
| Subscribe | `session/interrupt` | `{}` | → stops current TTS playback |
| Subscribe | `tts/stop` | `{}` | → stops current TTS playback |

## Extending

- **Replace STT**: implement `STTEngine` interface with `transcribe(audio) → str` in `stt_engine.py`
- **Replace TTS**: implement `TTSEngine` interface with `speak(text, blocking=True) → None` in `tts_engine.py`
- **Change wake word**: add model to OpenWakeWord and update `config.yaml::wake_word.model`
