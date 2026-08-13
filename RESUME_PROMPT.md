# Resume Prompt — Pi5 AI Camera

Copy and paste the block below to resume work exactly where we left off.

---

```
You are resuming work on a Raspberry Pi 5 AI Camera project.

## Project Status (Updated 2026-07-01)
Pi5 AI Camera — voice-interactive AI assistant for Raspberry Pi 5 (8GB)
with Hailo-10H AI HAT+ 2 (40 TOPS, 8GB dedicated RAM) and Camera Module 3.

GitHub: https://github.com/AtomCCCC/Pi5_AI_Camera
Pi path: /home/userpi/Desktop/MyProject/Pi5_AI_Camera/
Tailscale: pi5@100.66.5.5 (SSH key: ~/.ssh/pi_ai)
Pi sudo: userpi/1

## Completed (as of 2026-07-01)
- ✅ DeepSeek API tested — working with tool calls
- ✅ Hailo-10H NPU verified — firmware 5.1.1, /dev/hailo0
- ✅ hailo-apps installed (~/hailo-apps/venv_hailo_apps with --system-site-packages)
- ✅ hailo_ollama_proxy.py — HTTP proxy on port 8000 (plain chat → NPU, tool calls → CPU)
- ✅ HailoClient removed (no tool calling support, Ollama proxy is superior)
- ✅ OllamaClient updated — routes through NPU proxy, auto-fallback to CPU
- ✅ Router updated — DeepSeek / Hailo NPU / Ollama proxy triage
- ✅ GPIO service rewritten for Pi 5 (kernel PWM via /sys/class/pwm/pwmchip0, no pigpio)
- ✅ Servo mapping — GPIO13 (pwm1, Pin33), GPIO12 (pwm0, Pin32) with hardware PWM
- ✅ Tool definitions fixed — added "type":"function" + "function":{} wrapper
- ✅ README.md updated — added Section 13 Quick Start / Setup Guide
- ✅ All changes committed and pushed to GitHub (main branch)

## Architecture Decisions (updated)
- 2 LLM backends: DeepSeek (online), OllamaClient → NPU proxy :8000 (offline)
  - NPU proxy routes plain chat → Hailo-10H NPU (0% CPU), tool calls → CPU Ollama fallback
- GPIO: kernel PWM sysfs (no pigpio on Debian 13 Pi 5)
- Servos: GPIO13=servo1, GPIO12=servo2, RP1 hardware PWM via configured pwmchipN
- NPU Proxy: port 8000, wraps hailo_platform.genai.LLM as Ollama-compatible API
- MQTT: Mosquitto on localhost:1883, 5 services

## Open Decisions
1. VLM path: Hailo VLM (Path A, fast) vs Qwen2.5-VL-3B on CPU (Path B, slower)
2. Screen type: TBD
3. hailo-ollama binary — needs manual download from Hailo developer zone

## Next Steps
1. Test Vision Service with camera
2. Test Voice Service with STT/TTS
3. Test Session Manager
4. Full MQTT end-to-end test
5. Voice interrupt mechanism
6. Thermal testing (heat generation from NPU)
7. Create run_all.sh / systemd units for remaining services

## File Changes Since Initial Clone
- NEW: pi5_assistant/llm_orchestrator/hailo_ollama_proxy.py (NPU proxy on port 8000)
- NEW: opencode.json (remote SSH config)
- REMOVED: pi5_assistant/llm_orchestrator/hailo_client.py (replaced by Ollama proxy)
- MODIFIED: pi5_assistant/llm_orchestrator/tool_definitions.py (fixed format)
- MODIFIED: pi5_assistant/llm_orchestrator/ollama_client.py (NPU proxy routing)
- MODIFIED: pi5_assistant/llm_orchestrator/router.py (simplified: 2-backend routing)
- MODIFIED: pi5_assistant/llm_orchestrator/main.py (removed HailoClient, 2-backend triage)
- MODIFIED: pi5_assistant/llm_orchestrator/config.yaml (removed hailo section, proxy URL)
- MODIFIED: pi5_assistant/gpio_service/servo_controller.py (kernel PWM rewrite)
- MODIFIED: pi5_assistant/gpio_service/pin_config.py (gpiozero+lgpio rewrite)
- MODIFIED: pi5_assistant/gpio_service/main.py (PinConfig fix)
- MODIFIED: pi5_assistant/gpio_service/config.yaml (pin 18/12 mapping)
- MODIFIED: pi5_assistant/gpio_service/README.md (pigpio→kernel PWM)
- MODIFIED: pi5_assistant/README.md (pigpio→kernel PWM)
- MODIFIED: README.md (Section 13 Quick Start, pigpio→kernel PWM, status update)

## Credentials (DO NOT commit)
- DEEPSEEK_API_KEY: sk-80c4361aa25443bd83af2eb63a3f12cc
- GitHub token: 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
```
