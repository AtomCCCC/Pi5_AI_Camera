# Pi5 AI Camera

Voice-interactive AI assistant for **Raspberry Pi 5 (8GB)** with **Hailo-10H AI HAT+ 2 (40 TOPS)**.
Features real-time object detection, local/cloud LLM reasoning, voice interrupt, and GPIO servo/screen control.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          MQTT Bus (Mosquitto)                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌────┐ │
│  │  Voice   │  │  Vision  │  │     LLM      │  │   GPIO   │  │Sess│ │
│  │  Service │  │  Service │  │ Orchestrator │  │  Service  │  │Mgr │ │
│  │          │  │          │  │              │  │          │  │    │ │
│  │ Wake Word│  │ YOLOv8n  │  │ DeepSeek V4  │  │ Servo 1/2│  │Conv│ │
│  │ STT (Whis│  │ VLM (Hail│  │ ├─ online    │  │ Screen   │  │Intr│ │
│  │ TTS (Pipe│  │ SharedBuf│  │ └─ NPU proxy  │  │ GPIO pins│  │    │ │
│  └────┬─────┘  └────┬─────┘  └──────┬───────┘  └────┬─────┘  └─┬──┘ │
│       │             │               │               │          │     │
│       └─────┬───────┴───────┬───────┴───────┬───────┴──────────┘     │
│             │               │               │                        │
│        ┌────▼────┐    ┌────▼────┐     ┌─────▼─────┐                 │
│        │Camera   │    │ Mic/    │     │ Servos    │                 │
│        │Module 3 │    │ Speaker │     │ GPIO/Screen│                 │
│        └─────────┘    └─────────┘     └───────────┘                 │
└─────────────────────────────────────────────────────────────────────┘
```

## Project Structure

```
pi5_assistant/
├── pi5_assistant/            # Shared package
│   ├── __init__.py
│   └── mqtt_client.py        # MQTT wrapper (pub/sub for all services)
│
├── voice_service/            # Voice I/O
│   ├── main.py               # Entry point, MQTT loop
│   ├── wake_detector.py      # OpenWakeWord — always-on, 3-8% CPU idle
│   ├── stt_engine.py         # faster-whisper (default) / Vosk (fallback)
│   ├── tts_engine.py         # Piper TTS (local, ~200ms per response)
│   └── config.yaml
│
├── vision_service/           # Camera & vision pipeline
│   ├── main.py               # Entry point, continuous background thread
│   ├── detection_pipeline.py # YOLOv8n → Hailo-10H (430+ FPS)
│   ├── vlm_engine.py         # VLM: Hailo VLM (Path A) or Qwen2.5-VL (Path B)
│   ├── shared_buffer.py      # Thread-safe shared frame buffer
│   └── config.yaml
│
├── llm_orchestrator/         # AI reasoning
│   ├── main.py               # Entry point, tool loop
│   ├── deepseek_client.py    # DeepSeek V4 Flash API (online)
│   ├── ollama_client.py      # Qwen 2.5 via NPU proxy / CPU Ollama
│   ├── hailo_ollama_proxy.py # HTTP proxy :8000 — NPU for chat, CPU for tools
│   ├── router.py             # Connectivity detection, auto-switch
│   ├── tool_definitions.py   # 5 tool schemas (OpenAI-compatible)
│   ├── tool_handlers/        # One file per tool
│   │   ├── visual_detect.py  #   → vision/detect MQTT
│   │   ├── vlm_query.py      #   → vision/query MQTT
│   │   ├── servo_write.py    #   → gpio/command MQTT
│   │   ├── gpio_write.py     #   → gpio/command MQTT
│   │   └── screen_display.py #   → gpio/command MQTT
│   └── config.yaml
│
├── gpio_service/             # Physical outputs
│   ├── main.py               # Entry point
│   ├── servo_controller.py   # Kernel PWM (GPIO 18→pwm2, GPIO 12→pwm0)
│   ├── screen_driver.py       # OLED / TFT / HDMI / LCD abstraction
│   ├── pin_config.py          # gpiozero + lgpio digital I/O
│   └── config.yaml
│
├── session_manager/          # Session lifecycle
│   ├── main.py               # Entry point, interrupt handling
│   ├── conversation_store.py # In-memory per-session history with TTL
│   ├── interrupt_handler.py  # Wake-word interrupt → kill TTS → new session
│   └── config.yaml
│
├── docker-compose.yml        # Multi-service container
└── README.md                 # You are here
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| **NPU** | Hailo-10H (40 TOPS, 8GB dedicated RAM) |
| **CPU** | Raspberry Pi 5 (Cortex-A76 × 4, 8GB) |
| **Camera** | Raspberry Pi Camera Module 3 (IMX708, 30fps native, dynamic FPS/resolution) |
| **Object Detection** | YOLOv8n → Hailo-10H (430+ FPS @ 640×640, resized from any capture res) |
| **VLM (Path A)** | Hailo VLM on NPU (fast, zero-CPU load) |
| **VLM (Path B)** | Qwen2.5-VL-3B on CPU via Ollama (fallback) |
| **LLM (Online)** | DeepSeek V4 Flash API ($0.14/M tokens) |
| **LLM (Offline)** | Qwen 2.5 1.5B on Hailo-10H via NPU proxy:8000 (chat) / CPU Ollama:11434 (tools) |
| **Inter-service** | MQTT via Mosquitto |
| **Wake Word** | OpenWakeWord |
| **STT** | faster-whisper / Vosk |
| **TTS** | Piper TTS |
| **Servo PWM** | Kernel PWM via `/sys/class/pwm/pwmchip0` (GPIO18/GPIO12) |
| **Screen** | SSD1306 OLED (I2C) / TFT / HDMI (TBD) |

## Quick Start

```bash
# 1. Install system dependencies
sudo apt install mosquitto mosquitto-clients python3-pip python3-venv
sudo systemctl enable mosquitto

# 2. Clone and setup
git clone https://github.com/AtomCCCC/Pi5_AI_Camera.git
cd Pi5_AI_Camera
python3 -m venv .venv
source .venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Export environment
export DEEPSEEK_API_KEY="your_key_here"   # Required for online LLM

# 5. Start required services (each in its own terminal)
sudo systemctl start mosquitto           # MQTT broker
sudo systemctl start hailo-ollama-proxy  # NPU proxy on :8000

python -m voice_service.main             # Voice I/O
python -m vision_service.main            # Camera + YOLO + VLM
python -m llm_orchestrator.main          # AI reasoning
python -m gpio_service.main              # Servos + screen
python -m session_manager.main           # Session lifecycle
```

## Developing & Extending

### Adding a new tool (LLM → hardware)

1. Define the schema in `llm_orchestrator/tool_definitions.py`
2. Create a handler in `llm_orchestrator/tool_handlers/<name>.py`
3. Register the handler in `llm_orchestrator/main.py` (add to `tool_handlers` dict)
4. Implement the MQTT subscriber in the target service

### Adding a new service

1. Create `new_service/` with `main.py` and `config.yaml`
2. Import and use `MQTTClient` from `pi5_assistant.mqtt_client`
3. Add the container to `docker-compose.yml`

### Configuration

Each service has its own **`config.yaml`** — edit directly for:
- MQTT broker address/port
- Model selection (online vs offline LLM, VLM path)
- GPIO pins, servo pulse ranges, screen type
- Session timeout, history depth

## MQTT Topic Map

| Topic | Publisher | Subscriber | Payload |
|-------|-----------|------------|---------|
| `voice/transcript` | Voice Service | Session Manager | `{text, session_id}` |
| `command/in` | Session Manager | LLM Orchestrator | `{text, session_id}` |
| `response/out` | LLM Orchestrator | Voice Service | `{text, session_id}` |
| `vision/detect` | LLM Orchestrator | Vision Service | `{classes, min_confidence, session_id}` |
| `vision/detect_result` | Vision Service | LLM Orchestrator | `{detections[...], session_id}` |
| `vision/query` | LLM Orchestrator | Vision Service | `{prompt, session_id}` |
| `vision/result` | Vision Service | LLM Orchestrator | `{description, session_id}` |
| `gpio/command` | LLM Orchestrator | GPIO Service | `{type, ...params, session_id}` |
| `session/create` | Session Manager | (broadcast) | `{session_id, reason}` |
| `session/end` | Session Manager | (broadcast) | `{session_id, reason}` |
| `interrupt` | Voice Service | Session Manager | `{}` |
| `tts/stop` | Session Manager | Voice Service | `{}` |

## License

MIT
