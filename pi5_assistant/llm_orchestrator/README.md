# LLM Orchestrator

Central AI reasoning engine — routes to DeepSeek V4 (online) or Qwen (offline), executes tool calls in a loop, and publishes the final text response.

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

## Processing Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       LLM ORCHESTRATOR METHODOLOGY                           │
│                                                                              │
│  ┌──────────────────────┐                                                    │
│  │ command/in arrives   │  {text: "What's in front of me?", session_id}     │
│  └──────────┬───────────┘                                                    │
│             ▼                                                                │
│  ┌──────────────────────┐                                                    │
│  │ Router decides       │───── online? ───► DeepSeek V4 Flash API            │
│  │                      │───── offline? ──► Qwen 2.5 1.5B on Hailo via Ollama│
│  └──────────┬───────────┘                                                    │
│             ▼                                                                │
│  ┌──────────────────────────────────────────────────────────────┐           │
│  │                    TOOL LOOP                                  │           │
│  │                                                               │           │
│  │  ┌──────────────┐    ┌───────────────┐    ┌───────────────┐  │           │
│  │  │ LLM call #1  │───►│ Tool call     │───►│ Tool handler  │  │           │
│  │  │ with tools[] │    │ detected?     │    │ executes via  │  │           │
│  │  └──────────────┘    └───────┬───────┘    │ MQTT         │  │           │
│  │                              │ no         └───────┬───────┘  │           │
│  │                              ▼                    │ result   │           │
│  │  ┌──────────────┐          ┌──────────────────┐   │          │           │
│  │  │ FINAL TEXT   │◄─────────│ LLM call #2..N   │◄──┘          │           │
│  │  │ → response/out│          │ with tool results │              │           │
│  │  └──────────────┘          └──────────────────┘              │           │
│  └──────────────────────────────────────────────────────────────┘           │
│                                                                              │
│  Subscribed:                    Published:                                   │
│  • command/in ← Session Mgr     • response/out → Voice Service              │
│                                  • vision/detect → Vision Service            │
│                                  • vision/query  → Vision Service            │
│                                  • gpio/command  → GPIO Service              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Step-by-Step

1. **Command Received** — `main.py` receives `{text, session_id}` from `command/in`.
2. **Router Check** — `router.py` pings `api.deepseek.com:443`. If reachable → use DeepSeek V4 Flash (online, fast, cheap). If unreachable → use Qwen 2.5 1.5B on Hailo-10H via Ollama (offline fallback).
3. **First LLM Call** — Messages = `[system prompt, user text]` + `tools[]`. The LLM either returns text or a tool call.
4. **Tool Loop** — If a tool call is returned:
   - The tool name + arguments are matched to a handler in `tool_handlers/`
   - The handler publishes an MQTT request to the target service (Vision or GPIO)
   - The handler waits for the response (blocking with timeout)
   - The tool result is appended to messages as a `tool` role
   - A second LLM call is made with the tool results
   - Loop until the LLM returns plain text
5. **Response Published** — The final text is published to `response/out` for TTS.

### Tool Dispatch Map

| Tool Name | Handler | MQTT Topic | Target Service |
|-----------|---------|------------|----------------|
| `visual_detect` | `visual_detect.py` | `vision/detect` + `vision/detect_result` | Vision |
| `vlm_query` | `vlm_query.py` | `vision/query` + `vision/result` | Vision |
| `servo_write` | `servo_write.py` | `gpio/command` | GPIO |
| `gpio_write` | `gpio_write.py` | `gpio/command` | GPIO |
| `screen_display` | `screen_display.py` | `gpio/command` | GPIO |

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
