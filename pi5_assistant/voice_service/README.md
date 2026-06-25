# Voice Service

Handles voice I/O: wake word detection, speech-to-text, and text-to-speech. This is the user's primary interface — everything starts with a wake word and ends with a spoken response.

## Files

| File | Role |
|------|------|
| `main.py` | Entry point, subscribes to `response/out` and `tts/stop`, publishes `voice/transcript` |
| `wake_detector.py` | Porcupine or custom wake word engine — detects keyword and triggers session |
| `stt_engine.py` | Whisper.cpp — converts mic audio to text after wake word |
| `tts_engine.py` | Piper TTS (local) or ElevenLabs API — speaks LLM responses |
| `config.yaml` | Model paths, mic/speaker device, wake word sensitivity |

## Processing Flow

```
┌──────────────────────────────────────────────────────────────────┐
│                      VOICE SERVICE METHODOLOGY                    │
│                                                                   │
│  ┌──────────────┐    ┌──────────────────┐    ┌─────────────────┐  │
│  │ WAKE WORD    │    │ STT (Whisper)    │    │ TTS (Piper/     │  │
│  │ Detection    │───►│ Transcription    │───►│ ElevenLabs)     │  │
│  │              │    │                  │    │                 │  │
│  │ Porcupine    │    │ Whisper.cpp      │    │ Piper TTS       │  │
│  │ continuously │    │ on CPU/Hailo     │    │ (local, fast)   │  │
│  │ listens on   │    │ converts audio   │    │ or ElevenLabs   │  │
│  │ microphone   │    │ buffer to text   │    │ (cloud, HD)     │  │
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

1. **Idle** — `wake_detector.py` continuously listens on the microphone for the wake word (e.g. "Hey Pi").
2. **Wake Word Detected** — If a previous session is still speaking, the service publishes `interrupt` to kill TTS and trigger a new session.
3. **Recording** — Mic starts recording audio until a silence/pause is detected (VAD).
4. **STT** — `stt_engine.py` sends the audio buffer to Whisper.cpp, which returns transcribed text.
5. **Publish** — Text is published as `voice/transcript` for the Session Manager to forward to the LLM.
6. **Wait for Response** — The service subscribes to `response/out`, waiting for the LLM's final answer.
7. **TTS** — `tts_engine.py` converts the response text to speech via Piper (local) or ElevenLabs (cloud).
8. **Speak** — Audio plays out of the speaker. If interrupted by a new wake word, go to step 2.

## MQTT

| Direction | Topic | Payload | When |
|-----------|-------|---------|------|
| Publish | `voice/transcript` | `{text, session_id}` | After STT completes |
| Publish | `interrupt` | `{}` | New wake word during active TTS |
| Subscribe | `response/out` | `{text, session_id}` | → speaks the response |
| Subscribe | `tts/stop` | `{}` | → stops current TTS playback |

## Extending

- **Replace STT**: implement `STTEngine` interface with `transcribe(audio) → str`
- **Replace TTS**: implement `TTSEngine` interface with `speak(text) → None`
- **Add wake word**: add model file to `wake_detector.py` config
