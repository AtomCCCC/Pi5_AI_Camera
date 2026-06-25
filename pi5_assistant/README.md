# Pi 5 AI Assistant

Voice-interactive AI assistant for Raspberry Pi 5 with Hailo-10H accelerator.

## Services

| Service | Dir | Role |
|---------|-----|------|
| Voice | `voice_service/` | Wake word, STT, TTS |
| Vision | `vision_service/` | YOLO detection, VLM |
| LLM | `llm_orchestrator/` | DeepSeek API, Qwen local |
| GPIO | `gpio_service/` | Servos, screen, pins |
| Session | `session_manager/` | History, interrupts |

## Quick Start

```bash
# Install system deps
sudo apt install mosquitto python3-pip

# Install service deps
pip install -r requirements.txt

# Run all services
./run_all.sh
```

See `PI5_AI_ASSISTANT.md` for full architecture.
