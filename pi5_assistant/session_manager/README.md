# Session Manager

Manages conversation sessions and voice interrupts.

## Files

| File | Role |
|------|------|
| `main.py` | Entry point, subscribes to voice transcript, manages session lifecycle |
| `conversation_store.py` | In-memory per-session message history with TTL auto-cleanup |
| `interrupt_handler.py` | Handles new wake-word while speaking — kills TTS, ends current session, creates new one |
| `config.yaml` | Session timeout, max history turns, interrupt behavior |

## MQTT

| Direction | Topic | Payload |
|-----------|-------|---------|
| Subscribe | `voice/transcript` | `{text, session_id}` → forwards to `command/in` |
| Subscribe | `interrupt` | `{}` → triggers interrupt flow |
| Subscribe | `session/create` | `{session_id}` → creates new session |
| Publish | `command/in` | `{text, session_id}` → to LLM Orchestrator |
| Publish | `tts/stop` | `{}` → to Voice Service (kill current speech) |
| Publish | `session/end` | `{session_id, reason}` → broadcast |

## Interrupt Flow

```
1. Voice Service detects new wake word while LLM is responding
2. Voice Service publishes `interrupt` → Session Manager
3. Session Manager publishes `tts/stop` → Voice Service stops speaking
4. Session Manager ends current session, creates new one
5. Next voice input goes to the new session
```

## Extending

- **Persist conversations**: add SQLite/Redis backend in `conversation_store.py`
- **Multi-turn**: increase `max_history` in `config.yaml` and add context in `main.py:_on_user_text`
- **Custom interrupt**: modify `interrupt_handler.py` to support `"continue"` mode instead of `"new_session"`
