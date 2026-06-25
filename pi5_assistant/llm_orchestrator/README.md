# LLM Orchestrator

Central AI reasoning engine — routes to DeepSeek V4 (online) or Qwen (offline),
executes tool calls, and loops until the LLM produces a final text response.

## Files

| File | Role |
|------|------|
| `main.py` | Entry point, tool loop, MQTT subscriber to `command/in` |
| `deepseek_client.py` | DeepSeek V4 Flash/Pro API via OpenAI-compatible client |
| `ollama_client.py` | Qwen 2.5 1.5B on Hailo-10H via Ollama (with CPU fallback) |
| `router.py` | Pings `api.deepseek.com:443`, caches result for 30s, auto-switches backend |
| `tool_definitions.py` | 5 tool schemas in OpenAI JSON Schema format |
| `tool_handlers/` | One file per tool — dispatches via MQTT to the right service |
| `config.yaml` | API keys, model names, MQTT topics, system prompt |

## Tool Flow

```
User: "What's in front of me?"
                        ↓
   LLM ← visual_detect() → tool_handlers/visual_detect.py → MQTT → Vision Service
                        ↓
           LLM receives detections as text
                        ↓
   LLM ← vlm_query() → tool_handlers/vlm_query.py → MQTT → Vision Service (VLM)
                        ↓
         LLM produces: "I see a person and a car."
                        ↓
               Published to response/out
```

## Adding a New Tool

1. **Define schema** in `tool_definitions.py` (OpenAI `tools` format)
2. **Create handler** in `tool_handlers/<name>.py` with a `handle(arguments, session_id) → str` method
3. **Register** in `main.py` by adding to the `tool_handlers` dict
4. **Implement** the MQTT subscriber in the target service

## Configuration

Edit `config.yaml` to:
- Switch online model (`deepseek-v4-flash` vs `deepseek-v4-pro`)
- Change offline model / fallback model
- Adjust connectivity check interval
- Modify system prompt
