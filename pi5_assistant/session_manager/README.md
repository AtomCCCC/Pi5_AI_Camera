# Session Manager

Manages conversation sessions and voice interrupts. Each voice command creates a new session. If the user says the wake word while the assistant is speaking, the current session is interrupted and a new one begins.

## Files

| File | Role |
|------|------|
| `main.py` | Entry point, subscribes to voice transcript, manages session lifecycle |
| `conversation_store.py` | In-memory per-session message history with TTL auto-cleanup |
| `interrupt_handler.py` | Handles new wake-word while speaking — kills TTS, ends current session, creates new one |
| `config.yaml` | Session timeout, max history turns, interrupt behavior |

## Processing Flow

```
┌────────────────────────────────────────────────────────────────────────────┐
│                      SESSION MANAGER METHODOLOGY                            │
│                                                                             │
│  ┌──────────────────────┐       ┌──────────────────────┐                    │
│  │ voice/transcript     │       │ interrupt            │                    │
│  │ arrives from         │       │ arrives from         │                    │
│  │ Voice Service        │       │ Voice Service        │                    │
│  └──────────┬───────────┘       └──────────┬───────────┘                    │
│             ▼                              ▼                                │
│  ┌──────────────────────┐       ┌──────────────────────┐                    │
│  │ Normal Flow          │       │ Interrupt Flow       │                    │
│  │                      │       │                      │                    │
│  │ 1. Get or create     │       │ 1. Publish tts/stop  │                    │
│  │    session_id        │       │ 2. End current sess  │                    │
│  │ 2. Store user msg    │       │ 3. Generate new ID   │                    │
│  │ 3. Publish to        │       │ 4. Create new sess   │                    │
│  │    command/in        │       │ 5. Publish sessions  │                    │
│  │                      │       │                      │                    │
│  └──────────┬───────────┘       └──────────┬───────────┘                    │
│             ▼                              ▼                                │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    CONVERSATION STORE                                 │  │
│  │                                                                       │  │
│  │  ┌────────────┐   ┌────────────┐   ┌────────────┐                    │  │
│  │  │ Session A  │   │ Session B  │   │ Session C  │  ... auto-evicted  │  │
│  │  │ (active)   │   │ (expired)  │   │ (active)   │  after timeout     │  │
│  │  │ msg[],     │   │ msg[],     │   │ msg[],     │                    │  │
│  │  │ last_active│   │ last_active│   │ last_active│                    │  │
│  │  └────────────┘   └────────────┘   └────────────┘                    │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  Subscribed:                          Published:                            │
│  • voice/transcript ← Voice Service   • command/in  → LLM Orchestrator     │
│  • interrupt        ← Voice Service   • tts/stop    → Voice Service        │
│  • session/create   ← self            • session/end → broadcast            │
└────────────────────────────────────────────────────────────────────────────┘
```

### Normal Flow (Step-by-Step)

1. **Transcript Arrives** — Voice Service publishes `{text, session_id}` on `voice/transcript`.
2. **Session Lookup** — If `session_id` is provided, use it. If not, generate a new UUID and create a session.
3. **Store Message** — `conversation_store.py` saves the user message in the session's history (with `last_active` timestamp for TTL tracking).
4. **Forward** — The text is published to `command/in` for the LLM Orchestrator.

### Interrupt Flow (Step-by-Step)

1. **Interrupt Arrives** — Voice Service publishes `{}` on `interrupt` (new wake word detected while TTS active).
2. **Kill TTS** — `interrupt_handler.py` publishes `tts/stop` so the Voice Service stops speaking immediately.
3. **End Session** — The current session is ended and broadcast as `session/end`.
4. **New Session** — A new UUID is generated and broadcast as `session/create`.
5. **Next Input** — The next `voice/transcript` message goes to the new session.

## MQTT

| Direction | Topic | Payload | When |
|-----------|-------|---------|------|
| Subscribe | `voice/transcript` | `{text, session_id}` | STT result from Voice Service |
| Subscribe | `response/out` | `{text, session_id}` | Store the LLM reply in conversation history |
| Subscribe | `interrupt` | `{}` | New wake word during TTS |
| Subscribe | `session/create` | `{session_id}` | Internal session creation |
| Publish | `command/in` | `{text, session_id, history}` | → LLM Orchestrator |
| Publish | `tts/stop` | `{}` | → Voice Service (kill speech) |
| Publish | `session/end` | `{session_id, reason}` | → broadcast |

## Extending

- **Persist conversations**: add SQLite/Redis backend in `conversation_store.py`
- **Multi-turn**: increase `max_history` in `config.yaml` and add context in `main.py:_on_user_text`
- **Custom interrupt**: modify `interrupt_handler.py` to support `"continue"` mode instead of `"new_session"`
