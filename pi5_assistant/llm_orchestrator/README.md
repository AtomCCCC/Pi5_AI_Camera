# LLM Orchestrator

Central AI reasoning engine — routes to DeepSeek V4 (online) or Qwen via NPU proxy (offline), executes tool calls in a loop, and publishes the final text response.

## Files

| File | Role |
|------|------|
| `main.py` | Entry point, tool loop, MQTT subscriber to `command/in` |
| `deepseek_client.py` | DeepSeek V4 Flash/Pro API via OpenAI-compatible client |
| `ollama_client.py` | Routes through NPU proxy :8000 (chat→NPU, tools→CPU), falls back to CPU Ollama :11434 |
| `hailo_ollama_proxy.py` | HTTP proxy on port 8000 — Hailo-10H NPU for plain chat, CPU Ollama for tool calls |
| `router.py` | Pings `api.deepseek.com:443`, caches result for 30s, auto-switches backend (online→DeepSeek, offline→OllamaClient) |
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
│  │                      │───── offline? ──► OllamaClient → NPU proxy :8000   │
│  │                      │                    ├─ Plain chat → Hailo-10H NPU   │
│  │                      │                    └─ Tool calls → CPU Ollama :11434│
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
2. **Router Check** — `router.py` pings `api.deepseek.com:443`. If reachable → use DeepSeek V4 Flash (online). If unreachable → use `OllamaClient` which routes through the NPU proxy (`:8000`). The proxy handles plain chat on Hailo-10H NPU (0% CPU) and automatically falls back to CPU Ollama (`:11434`) for tool calls.
3. **First LLM Call** — Messages = `[system prompt, session history]` + `tools[]`. The LLM either returns text or a tool call. Command processing runs on a worker thread so the MQTT network loop can receive tool replies.
4. **Tool Loop** — If a tool call is returned:
   - The tool name + arguments are matched to a handler in `tool_handlers/`
   - The handler publishes an MQTT request to the target service (Vision or GPIO)
   - The handler waits for the response (blocking with timeout)
   - The tool result is appended to messages as a `tool` role
   - A second LLM call is made with the tool results
   - Loop until the LLM returns plain text (or reaches the configured tool-call limit)
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
- Change offline model / fallback model / proxy URL
- Adjust connectivity check interval
- Modify system prompt

### NPU Proxy Note

The `hailo_ollama_proxy.py` listens on port 8000 and wraps the Hailo-10H NPU LLM as an Ollama-compatible API. When tool calls are detected in the request, it proxies to CPU Ollama on port 11434 automatically. Run it as a systemd service:

```bash
sudo cp hailo-ollama-proxy.service /etc/systemd/system/
sudo systemctl enable hailo-ollama-proxy && sudo systemctl start hailo-ollama-proxy
```
