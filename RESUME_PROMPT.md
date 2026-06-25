# Resume Prompt — Pi5 AI Camera

Copy and paste the block below to resume work exactly where we left off.

---

```
You are resuming work on a Raspberry Pi 5 AI Camera project.

## Project
Pi5 AI Camera — voice-interactive AI assistant for Raspberry Pi 5 (8GB)
with Hailo-10H AI HAT+ 2 (40 TOPS, 8GB dedicated RAM) and Camera Module 3.

GitHub: https://github.com/AtomCCCC/Pi5_AI_Camera
Local:  /home/atomcccc/Desktop/Opencode/

## What Has Been Built (all 6 services, all files written)

Root:
- README.md — full architecture document (12 sections)
- pi5_assistant/README.md — service overview, structure, tech stack, MQTT topic map
- RESUME_PROMPT.md — this file
- .gitignore

Shared package (pi5_assistant/):
- __init__.py
- mqtt_client.py — MQTT wrapper with auto-JSON pub/sub

Voice Service (voice_service/):
- main.py, wake_detector.py, stt_engine.py, tts_engine.py, config.yaml, README.md

Vision Service (vision_service/):
- main.py, detection_pipeline.py, vlm_engine.py, shared_buffer.py, config.yaml, README.md
- Dynamic FPS/resolution: motion differencing → switches between 640×640@30fps (low motion)
  and 640×320@60fps (high motion). YOLO always runs at 640×640 regardless.

LLM Orchestrator (llm_orchestrator/):
- main.py, deepseek_client.py, ollama_client.py, router.py, tool_definitions.py, config.yaml, README.md
- tool_handlers/: visual_detect.py, vlm_query.py, servo_write.py, gpio_write.py, screen_display.py

GPIO Service (gpio_service/):
- main.py, servo_controller.py, screen_driver.py, pin_config.py, config.yaml, README.md

Session Manager (session_manager/):
- main.py, conversation_store.py, interrupt_handler.py, config.yaml, README.md

## Architecture Decisions (locked)
- 5 independent services communicating via MQTT (Mosquitto)
- LLM: DeepSeek V4 Flash (online) / Qwen 2.5 1.5B on Hailo (offline), auto-routed by router.py
- Vision: continuous YOLO background thread + shared buffer (no re-inference on tool calls)
- Voice interrupt: new wake word → kill TTS → new session
- STT: Whisper.cpp / TTS: Piper (local) or ElevenLabs (cloud)
- Servos: hardware PWM via pigpio on BCM 12/13
- Session: single-turn per voice command (simplest start)
- Camera Module 3: dynamic FPS/resolution by motion detection

## Open Decisions (needs team discussion)
1. VLM path: Hailo VLM (Path A, fast) vs Qwen2.5-VL-3B on CPU (Path B, slower but open)
2. Screen type: I2C OLED (SSD1306) / SPI TFT (ILI9341) / HDMI / Character LCD

## Next Steps
1. Create requirements.txt with all Python dependencies
2. Create run_all.sh / systemd service files for auto-start
3. Phase 1 implementation: OS setup, Hailo-10H driver install, YOLO test, Mosquitto setup
4. Phase 2: LLM orchestration end-to-end testing
5. Phase 3: Voice pipeline integration
6. Phase 4: Vision pipeline + dynamic FPS testing
7. Phase 5: GPIO hardware integration
8. Phase 6: Full integration, thermal testing, systemd units

## Credentials (DO NOT commit)
- GitHub token is in /home/atomcccc/Desktop/api_key.txt
- DEEPSEEK_API_KEY needed as env var at runtime
```
