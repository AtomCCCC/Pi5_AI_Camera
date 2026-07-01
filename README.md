# Pi5 AI Camera — Complete Architecture Document

> **Author:** AI-assisted design  
> **Date:** 2026-07-01  
> **Status:** Development — LLM backends + GPIO tested on hardware  
> **Hardware:** Pi 5 (8GB) + AI HAT+ 2 (Hailo-10H, 40 TOPS) + Camera Module 3

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Hardware & Component Map](#2-hardware--component-map)
3. [Final Architecture](#3-final-architecture)
4. [LLM API Context & Function Calling](#4-llm-api-context--function-calling)
5. [Service Breakdown](#5-service-breakdown)
   - [5.1 Voice Service](#51-voice-service)
   - [5.2 Vision Service](#52-vision-service)
   - [5.3 LLM Orchestrator](#53-llm-orchestrator)
   - [5.4 GPIO Service](#54-gpio-service)
   - [5.5 Session Manager](#55-session-manager)
6. [Voice Interrupt Mechanism](#6-voice-interrupt-mechanism)
7. [Vision Persistence (Background Thread)](#7-vision-persistence-background-thread)
8. [VLM Path A vs Path B](#8-vlm-path-a-vs-path-b)
9. [Hailo-10H Setup Guide](#9-hailo-10h-setup-guide)
10. [Implementation Roadmap](#10-implementation-roadmap)
11. [Full Data Flow Example](#11-full-data-flow-example)
12. [Team Discussion Questions](#12-team-discussion-questions)
13. [Quick Start / Setup Guide](#13-quick-start--setup-guide)

---

## 1. System Overview

This project turns a Raspberry Pi 5 into a **voice-interactive AI assistant** with:

- **Real-time object detection** at 30+ FPS via the Hailo-10H NPU, with dynamic camera FPS/resolution adjustment based on motion
- **Vision-language understanding** (two interchangeable paths)
- **Voice control** with interrupt capability (new command cancels current action)
- **LLM-powered reasoning** via DeepSeek V4 API (online) or local Qwen (offline)
- **GPIO control** for 2 servo motors + an undecided screen
- **Tool-calling pattern** — the LLM decides when to invoke vision, GPIO, or display functions
- **Modular services** communicating via MQTT for team-based development

---

## 2. Hardware & Component Map

### Bill of Materials

| Component | Model / Spec | Role | Status |
|-----------|-------------|------|--------|
| SBC | Raspberry Pi 5, 8GB RAM | Host OS, voice pipeline, orchestration, GPIO | Confirmed |
| AI Accelerator | AI HAT+ 2 (Hailo-10H), 40 TOPS, 8GB LPDDR4X | YOLO detection, VLM, local LLM inference | Confirmed |
| Camera | Raspberry Pi Camera Module 3 (IMX708) | Real-time video input, dynamic FPS/resolution (30fps @ 640×640 idle, 60fps @ 640×320 on motion) | Confirmed |
| Microphone | USB class-compliant microphone | Voice capture | Needed |
| Speaker | 3.5mm or USB speaker | Audio output | Needed |
| Cooling | Active Cooler (official or third-party) | Mandatory — Pi 5 throttles without airflow on sustained load | Needed |
| Storage | NVMe SSD (≥256GB) via PCIe HAT | Faster model loading, swap space, session DB | Recommended |
| Servo 1 | Standard servo (e.g. SG90, MG995) | Physical actuation — pin TBD | Needed |
| Servo 2 | Standard servo (e.g. SG90, MG995) | Physical actuation — pin TBD | Needed |
| Screen | TBD (see options below) | Local display | TBD |

### Compute Resource Map

| Task | Runs On | Performance | CPU Load |
|------|---------|-------------|----------|
| YOLOv8n object detection (640×640) | Hailo-10H NPU | 430+ FPS (YOLO always @ 640×640, camera capture resized) | 0% |
| YOLOv8s object detection (640×640) | Hailo-10H NPU | 500+ FPS | 0% |
| VLM scene understanding (Path A) | Hailo-10H NPU + 8GB RAM | ~1-3s latency | 0% |
| LLM: Qwen 2.5 1.5B (offline) | Hailo-10H NPU + 8GB RAM | 20-35 tok/s | 0% |
| LLM: Llama 3.2 1B (offline) | Hailo-10H NPU + 8GB RAM | 30-50 tok/s | 0% |
| VLM: Qwen2.5-VL-3B (Path B) | Pi 5 CPU | 2-5 tok/s, ~5-15s latency | ~100% of 1 core |
| LLM: Qwen 2.5 3B (CPU fallback) | Pi 5 CPU | 2-5 tok/s | ~100% of 1 core |
| LLM: DeepSeek V4 API (online) | Cloud (API call) | Depends on network | Minimal |
| Wake word detection | Pi 5 CPU | 3-8% CPU idle | Low |
| STT: faster-whisper tiny | Pi 5 CPU | ~1s per utterance | Moderate |
| STT: Vosk (offline fallback) | Pi 5 CPU | <1s per utterance | Low |
| TTS: Piper | Pi 5 CPU | ~200ms per response | Low |
| Orchestration + MQTT | Pi 5 CPU | Negligible | Low |

### Screen Options (TBD)

| Type | Pins Needed | Library | Pros | Cons |
|------|------------|---------|------|------|
| I2C OLED (128×64, SSD1306) | 2 (SDA, SCL) | `luma.oled` | Easy wiring, small footprint | Low resolution, monochrome |
| SPI TFT (240×320, ILI9341) | 5 (MOSI, MISO, SCLK, CS, DC) | `fbdev` / `pygame` | Faster refresh, color | More pins, more wiring |
| HDMI monitor | 0 (HDMI port) | Any GUI | Best quality, no GPIO use | Bulky, power hungry |
| Character LCD (16×2, HD44780) | 6 (4-bit mode) | `RPLCD` | Simple, cheap | Very limited content |

---

## 3. Final Architecture

### High-Level Processing Planes

```
                         PLANE 1: Pi 5 CPU
            ┌─────────────────────────────────────┐
            │  Voice Pipeline    Orchestration     │
            │  ┌─────────────┐  ┌──────────────┐  │
            │  │ OpenWakeWord │  │ MQTT Broker  │  │
            │  │ (always-on)  │  │ (Mosquitto)  │  │
            │  └──────┬──────┘  └──────┬───────┘  │
            │         │               │           │
            │  ┌──────▼──────┐  ┌──────▼───────┐  │
            │  │ STT Engine  │  │ Tool Handler │  │
            │  │ (whisper)   │  │ (servo, vis) │  │
            │  └──────┬──────┘  └──────────────┘  │
            │         │                           │
            │  ┌──────▼──────┐                    │
            │  │ Piper TTS   │    GPIO Control    │
            │  └─────────────┘  ┌──────────────┐  │
            │                   │ RPi.GPIO     │  │
            │                   │ / lgpio      │  │
            │                   └──────┬───────┘  │
            │                          │          │
            │              ┌───────────▼────────┐ │
            │              │ Servos | Screen    │ │
            └──────────────┴────────────────────┘ │
                              │                   │
                              │ PCIe Gen 3        │
                              ▼                   │
                         PLANE 2: Hailo-10H NPU   │
            ┌────────────────────────────────┐    │
            │  Vision Pipeline              │    │
            │  ┌────────────────────────┐   │    │
            │  │ YOLOv8 (continuous)   │   │    │
            │  │ → shared_buffer       │   │    │
            │  └─────────┬─────────────┘   │    │
            │            │                  │    │
            │  ┌─────────▼─────────────┐   │    │
            │  │ VLM (on-demand)       │   │    │
            │  │ → scene descriptions  │   │    │
            │  └─────────┬─────────────┘   │    │
            │            │                  │    │
            │  ┌─────────▼─────────────┐   │    │
            │  │ LLM (Qwen 1.5B)      │   │    │
            │  │ → offline fallback   │   │    │
            │  └──────────────────────┘   │    │
            │  8GB Dedicated LPDDR4X      │    │
            └─────────────────────────────┘    │
            ───────────────────────────────────┘
```

### MQTT Service Bus

```
┌─────────────────────────────────────────────────────────────────┐
│                     MQTT BROKER (localhost:1883)                  │
│                                                                   │
│  Topics:                                                          │
│    command/in        ← Voice Service publishes transcribed text   │
│    response/out      → Voice Service receives TTS text           │
│    vision/query      → Vision Service receives VLM request       │
│    vision/result     ← Vision Service publishes VLM response     │
│    gpio/command      → GPIO Service receives actuation commands  │
│    gpio/status       ← GPIO Service publishes current state      │
│    session/control   → Session Manager receives lifecycle events │
│    system/health     ← All services publish heartbeats           │
│                                                                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │  Voice   │ │  Vision  │ │   LLM    │ │   GPIO   │ │Session │ │
│  │ Service  │ │ Service  │ │ Orch.    │ │ Service  │ │Manager │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### Why MQTT?

| Reason | Explanation |
|--------|------------|
| **Independent processes** | Each service crashes independently |
| **Team-friendly** | Members own separate services, different git repos |
| **Language agnostic** | Any service could be rewritten in C++/Rust later |
| **Distributable** | Could split across multiple Pis if needed |
| **Loggable** | Every message can be captured for debugging |
| **Standard IoT protocol** | Well-documented, many libraries |

---

## 4. LLM API Context & Function Calling

### Setting Context via LLM API

Both DeepSeek V4 and Qwen (via Ollama) support the same OpenAI-compatible pattern for setting system context and defining tools.

#### DeepSeek V4 API

```
Endpoint:  https://api.deepseek.com/v1/chat/completions
Models:    deepseek-v4-pro (1.6T params, 49B active)
           deepseek-v4-flash (284B params, 13B active)
Pricing:   Flash — $0.14 input / $0.28 output per 1M tokens
           Pro   — $1.74 input / $3.48 output per 1M tokens
Context:   1M tokens
Cache:     Cache hit gets 90% discount
Tool call: Up to 128 parallel function calls, OpenAI-compatible format
```

#### Pattern for Setting Context

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-...",
    base_url="https://api.deepseek.com"
)

# System message sets the assistant's behavior / context
messages = [
    {
        "role": "system",
        "content": (
            "You are a Raspberry Pi 5 AI assistant connected to a camera, "
            "2 servo motors, and a display. You can detect objects in real time, "
            "analyze scenes via vision-language model, move servos, and write to GPIO pins. "
            "Use the provided tools when the user asks about visual input or physical actions. "
            "When no internet is available, you fall back to a local Qwen model."
        )
    },
    {
        "role": "user",
        "content": "Is there a person in the frame?"
    }
]

# Tools array defines what the LLM can invoke
tools = [...]  # See Tool Definitions section

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=messages,
    tools=tools,
    tool_choice="auto",
)
```

#### Qwen via Ollama (Local Fallback)

```python
import ollama

response = ollama.chat(
    model="qwen2.5:1.5b",
    messages=[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
    tools=tools,        # Same tool definitions as DeepSeek
    tool_choice="auto",
)
```

The `router.py` in the LLM Orchestrator auto-detects internet connectivity and switches between DeepSeek and Qwen.

### Tool Definitions (Shared Across Both LLMs)

```python
tools = [
    {
        "name": "visual_detect",
        "description": "Get current object detections from the camera in real time",
        "parameters": {
            "type": "object",
            "properties": {
                "classes": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional filter: only return these object classes"
                },
                "min_confidence": {
                    "type": "number",
                    "description": "Minimum confidence (0.0-1.0), defaults to 0.5"
                }
            }
        }
    },
    {
        "name": "vlm_query",
        "description": "Ask a question about the current camera view (scene understanding)",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Question about the scene, e.g. 'What color is the car?' or 'How many people are there?'"
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "servo_write",
        "description": "Set a servo motor to a specific angle (0-180 degrees)",
        "parameters": {
            "type": "object",
            "properties": {
                "servo": {
                    "type": "integer", "enum": [1, 2],
                    "description": "Servo number: 1 or 2"
                },
                "angle": {
                    "type": "integer", "minimum": 0, "maximum": 180,
                    "description": "Target angle in degrees"
                }
            },
            "required": ["servo", "angle"]
        }
    },
    {
        "name": "gpio_write",
        "description": "Set a GPIO pin HIGH (true) or LOW (false)",
        "parameters": {
            "type": "object",
            "properties": {
                "pin": {
                    "type": "integer",
                    "description": "BCM GPIO pin number"
                },
                "value": {
                    "type": "boolean",
                    "description": "true = HIGH (3.3V), false = LOW (0V)"
                }
            },
            "required": ["pin", "value"]
        }
    },
    {
        "name": "screen_display",
        "description": "Display text content on the connected screen",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Text content to display"
                },
                "clear": {
                    "type": "boolean",
                    "description": "Whether to clear the screen first",
                    "default": True
                }
            },
            "required": ["content"]
        }
    }
]
```

---

## 5. Service Breakdown

### 5.1 Voice Service

**Purpose:** Always-listening voice interface — wake word, speech-to-text, text-to-speech.

```
voice_service/
├── main.py              # MQTT loop, coordinates wake/STT/TTS
├── wake_detector.py     # OpenWakeWord — always-on, 3-8% CPU idle
├── stt_engine.py        # faster-whisper tiny (default), Vosk (offline fallback)
├── tts_engine.py        # Piper TTS — ~200ms response generation
└── config.yaml          # Mic device ID, wake word model, TTS voice name
```

**config.yaml:**
```yaml
mic_device: "plughw:1,0"       # ALSA device for USB mic
wake_word_model: "hey_raspberry"  # Can train custom word
stt:
  engine: "faster-whisper"     # or "vosk" for lighter offline
  model: "tiny"                # tiny/en/base/small
  language: "en"
tts:
  engine: "piper"
  voice: "en_US-amy-medium"    # Piper voice pack
  speed: 1.0
```

**Flow:**
1. `wake_detector.py` runs in a thread, listening for wake word
2. On wake: captures audio → sends to STT → publishes `command/in`
3. Subscribes to `response/out` → Piper TTS plays response
4. On `session/interrupt`: stops TTS playback immediately

---

### 5.2 Vision Service

**Purpose:** Continuous real-time detection + on-demand VLM scene analysis.

```
vision_service/
├── main.py               # Camera init, Hailo pipeline start, MQTT loop
├── detection_pipeline.py # YOLOv8 YOLO on Hailo-10H, runs continuously
├── vlm_engine.py         # On-demand VLM — supports both Path A & Path B
├── shared_buffer.py      # Thread-safe storage for latest detections
└── config.yaml           # Camera params, model selection, thresholds
```

**config.yaml:**
```yaml
camera:
  sensor: "imx708"                 # Camera Module 3 sensor
  base_resolution: [640, 640]      # YOLO input size (always this for inference)
  base_framerate: 30
  dynamic_adjust:
    enabled: true
    check_interval: 0.5            # seconds between motion checks
    motion_threshold: 30           # mean pixel diff to trigger "high motion"
    motion_window: 5               # frames to average for smoothing
    profiles:
      low_motion:
        resolution: [640, 640]
        framerate: 30
        label: "full quality"
      high_motion:
        resolution: [640, 320]
        framerate: 60
        label: "fast capture"
detection:
  model: "yolov8n"           # yolov8n / yolov8s
  confidence: 0.5
  iou_threshold: 0.45
  backend: "hailo"           # Runs on Hailo-10H NPU
vlm:
  mode: "hailo"              # "hailo" (Path A) or "qwen-cpu" (Path B)
```

**`shared_buffer.py` details:**
```python
import threading
import time

class SharedDetectionBuffer:
    def __init__(self):
        self._lock = threading.Lock()
        self._detections = []
        self._timestamp = 0
        self._latest_frame = None

    def update(self, detections, frame):
        with self._lock:
            self._detections = detections
            self._timestamp = time.time()
            self._latest_frame = frame

    def get(self):
        with self._lock:
            return {
                "detections": self._detections.copy(),
                "timestamp": self._timestamp,
                "age_ms": (time.time() - self._timestamp) * 1000
            }
```

**Flow:**
1. `detection_pipeline.py` runs on the Hailo-10H in a continuous thread
2. Each frame: estimates motion via frame differencing (160×120 grayscale)
3. Every 0.5s: if average motion > threshold → switches to 640×320 @ 60fps; else back to 640×640 @ 30fps
4. Regardless of camera capture resolution, YOLO always runs inference at 640×640 (frame resized)
5. Results stored in `shared_buffer` with current motion profile info
6. Subscribes to `vision/query` → captures one frame → runs VLM → publishes `vision/result`
7. `visual_detect` tool handler reads `shared_buffer` instantly (no re-inference)

---

### 5.3 LLM Orchestrator

**Purpose:** Central AI reasoning — routes between DeepSeek (online) and Qwen (offline), manages tool calls.

```
llm_orchestrator/
├── main.py              # MQTT loop: subscribes command/in, publishes response/out
├── deepseek_client.py   # OpenAI-compatible client for DeepSeek V4 API
├── ollama_client.py     # Client for local Qwen on Hailo-10H (via Ollama)
├── tool_definitions.py  # All tool schemas (shared with both clients)
├── tool_handlers/
│   ├── visual_detect.py # Reads shared_buffer from Vision Service
│   ├── vlm_query.py     # Publishes vision/query, awaits vision/result
│   ├── servo_write.py   # Publishes to gpio/command
│   ├── gpio_write.py    # Publishes to gpio/command
│   └── screen_display.py # Publishes to gpio/command
├── router.py            # Auto-detect: online → DeepSeek, offline → Qwen
└── config.yaml          # API keys, model IDs, timeout settings
```

**config.yaml:**
```yaml
deepseek:
  api_key: "${DEEPSEEK_API_KEY}"  # Load from env variable
  model: "deepseek-v4-flash"       # or deepseek-v4-pro
  base_url: "https://api.deepseek.com"
  timeout: 30  # seconds

ollama:
  model: "qwen2.5:1.5b"           # Runs on Hailo-10H NPU
  base_url: "http://localhost:11434"
  fallback_model: "qwen2.5:3b"    # CPU fallback if Hailo not available

router:
  connectivity_check_interval: 30  # seconds between online checks
  prefer_online: true               # Use DeepSeek when internet available
```

**`router.py` logic:**

```python
def is_online():
    try:
        import socket
        socket.create_connection(("api.deepseek.com", 443), timeout=3)
        return True
    except OSError:
        return False

def get_llm_client():
    if is_online():
        return DeepSeekClient(model=config.deepseek.model)
    else:
        return OllamaClient(model=config.ollama.model)
```

**Flow:**
1. Subscribes to `command/in`
2. Calls LLM (DeepSeek or Qwen) with system prompt + conversation history + tools
3. If LLM returns text → publishes `response/out`
4. If LLM returns tool_calls → dispatches to appropriate `tool_handler/`
5. Collects results → sends back to LLM for final text response → publishes `response/out`

---

### 5.4 GPIO Service

**Purpose:** Physical I/O abstraction — servo PWM control, GPIO pin writes, screen display.

```
gpio_service/
├── main.py              # MQTT loop + GPIO init
├── servo_controller.py  # Hardware PWM for 2 servos
├── screen_driver.py     # Abstract screen interface (pluggable driver)
├── pin_config.py        # Logical name → physical pin mapping
└── config.yaml          # Pin assignments, servo limits, screen type
```

**config.yaml:**
```yaml
servos:
  servo_1:
    pin: 12               # BCM GPIO 12 (PWM0)
    min_angle: 0
    max_angle: 180
    min_pulse_us: 500     # Standard servo pulse range
    max_pulse_us: 2500
  servo_2:
    pin: 13               # BCM GPIO 13 (PWM1)
    min_angle: 0
    max_angle: 180
    min_pulse_us: 500
    max_pulse_us: 2500

screen:
  driver: "none"          # Options: "ssd1306", "hd44780", "tft", "hdmi", "none"
  # I2C OLED
  i2c_bus: 1
  i2c_address: 0x3C
  # SPI TFT
  spi_bus: 0
  spi_cs: 0
  spi_dc: 24
  # Character LCD
  rs_pin: 17
  e_pin: 18
  data_pins: [22, 23, 24, 25]
```

**`servo_controller.py` (using Pi 5 hardware PWM):**

```python
import pigpio  # PiGPIO daemon for hardware PWM

class ServoController:
    def __init__(self, config):
        self.pi = pigpio.pi()
        self.servos = config["servos"]

    def set_angle(self, servo_num, angle):
        servo = self.servos[f"servo_{servo_num}"]
        pulse = servo["min_pulse_us"] + (
            angle / servo["max_angle"] * 
            (servo["max_pulse_us"] - servo["min_pulse_us"])
        )
        self.pi.set_servo_pulsewidth(servo["pin"], pulse)
```

**`screen_driver.py` — Abstract Interface:**

```python
class ScreenDriver:
    """Abstract screen interface — implement for each display type."""
    def display_text(self, text: str, clear: bool = True):
        raise NotImplementedError
    def clear(self):
        raise NotImplementedError
    def display_image(self, image_path: str):
        raise NotImplementedError

class SSD1306Driver(ScreenDriver):
    """I2C OLED implementation."""
    def __init__(self, bus, address):
        from luma.oled.device import ssd1306
        from luma.core.interface.serial import i2c
        serial = i2c(port=bus, address=address)
        self.device = ssd1306(serial)
    def display_text(self, text, clear=True):
        # Render text on OLED
        pass
```

---

### 5.5 Session Manager

**Purpose:** Track conversation sessions, handle voice interrupts, maintain history.

```
session_manager/
├── main.py                 # MQTT loop
├── conversation_store.py   # SQLite-backed session history
├── interrupt_handler.py    # Manages interrupt lifecycle
└── config.yaml             # Limits and timeouts
```

**config.yaml:**
```yaml
sessions:
  max_history_turns: 10        # Number of user/assistant turns kept in context
  session_timeout_minutes: 30  # Auto-close idle sessions
  interrupt_timeout_ms: 2000   # Debounce interrupts

storage:
  db_path: "/var/lib/pi-assistant/sessions.db"
```

**`conversation_store.py` schema:**

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP,
    is_active BOOLEAN DEFAULT 1
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id),
    role TEXT NOT NULL,  -- 'user', 'assistant', 'tool'
    content TEXT,
    tool_calls TEXT,     -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Interrupt lifecycle:**
1. Voice Service detects new wake word during active TTS playback
2. Publishes `session/interrupt` with current `session_id`
3. Session Manager marks current session as inactive
4. LLM Orchestrator stops awaiting tool results, discards pending context
5. Session Manager creates a new session for the incoming command
6. Voice Service begins capturing the new utterance

---

## 6. Voice Interrupt Mechanism

### Thread Model

```
┌─────────────────────────────────────────────────────────────┐
│                    MAIN PROCESS                               │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Thread 1: Voice Listener                             │    │
│  │  ┌──────────┐   ┌────────────────────────┐          │    │
│  │  │ Wake Word│──→│ Audio Capture Buffer   │          │    │
│  │  │ Detector │   │ (circular, ~5 seconds) │          │    │
│  │  └──────────┘   └───────────┬────────────┘          │    │
│  │                             │                        │    │
│  │              ┌──────────────▼──────────────┐        │    │
│  │              │  STT (faster-whisper)       │        │    │
│  │              └──────────────┬──────────────┘        │    │
│  │                             │                        │    │
│  │              ┌──────────────▼──────────────┐        │    │
│  │              │  interrupt_flag = True       │────────┼──→│
│  │              │  Publish command/in          │        │    │
│  │              └──────────────────────────────┘        │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Thread 2: TTS Player                                 │    │
│  │  ┌────────────────────────────────────┐             │    │
│  │  │ Subscribes response/out           │             │    │
│  │  │ Plays TTS via Piper               │             │    │
│  │  │ Checks interrupt_flag before      │             │    │
│  │  │ each sentence                     │             │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Thread 3: Vision Pipeline (continuous)               │    │
│  │  ┌──────────────┐   ┌──────────────┐   ┌────────┐ │    │
│  │  │ Camera       │──→│ Hailo YOLO   │──→│ Shared │ │    │
│  │  │ @30 FPS      │   │ Inference    │   │ Buffer │ │    │
│  │  └──────────────┘   └──────────────┘   └────────┘ │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### Interrupt Logic

```python
# In Voice Service main loop
interrupt_flag = False

def on_wake_word():
    global interrupt_flag
    interrupt_flag = True
    # Immediately stop any TTS playback
    tts_engine.stop()
    # Capture audio and transcribe
    text = stt_engine.transcribe(audio_buffer.get())
    # Publish interrupt + new command
    mqtt.publish("session/interrupt", {"session_id": current_session})
    mqtt.publish("command/in", {"text": text, "session_id": new_uuid()})
    interrupt_flag = False

# In TTS playback loop
def play_response(text):
    sentences = split_sentences(text)
    for sentence in sentences:
        if interrupt_flag:
            break  # Stop mid-sentence
        audio = tts_engine.synthesize(sentence)
        play_audio(audio)
```

---

## 7. Vision Persistence (Background Thread)

The vision pipeline is **never interrupted** — it runs independently in its own thread from boot until shutdown.

```
┌─────────────────────────────────────────────────────────────┐
│  Vision Thread (started once, never stopped)                 │
│                                                               │
│  Loop:                                                        │
│    1. Capture frame from Camera Module 3                      │
│    2. Estimate motion (frame differencing @ 160×120 gray)     │
│    3. Every 0.5s: switch profile based on motion score:       │
│       Low motion  → 640×640 @ 30fps (full quality)           │
│       High motion → 640×320 @ 60fps (fast capture)           │
│    4. Resize frame to 640×640 for YOLO                        │
│    5. Send to Hailo-10H YOLO pipeline (430+ FPS)             │
│    6. Store in SharedDetectionBuffer (thread-safe)           │
│    7. Loop                                                   │
│                                                               │
│  shared_buffer contents:                                      │
│    {                                                          │
│      "detections": [                                          │
│        {"class": "person", "confidence": 0.95,                │
│         "bbox": [100, 200, 300, 400]},                        │
│        {"class": "chair", "confidence": 0.87,                 │
│         "bbox": [50, 300, 150, 450]}                          │
│      ],                                                       │
│      "profile": "high_motion",                                │
│      "motion_score": 42.5,                                    │
│      "timestamp": 1234567890.123,                             │
│      "age_ms": 15                                             │
│    }                                                          │
└─────────────────────────────────────────────────────────────┘

When LLM calls visual_detect():
  1. Tool handler reads shared_buffer.get()
  2. Returns instantly — no camera capture, no inference
  3. LLM receives structured detection data
```

---

## 8. VLM Path A vs Path B

### Comparison Table

| Aspect | Path A: Hailo VLM | Path B: Qwen2.5-VL-3B |
|--------|-------------------|----------------------|
| **Hardware** | Hailo-10H NPU + 8GB dedicated RAM | Pi 5 CPU + system RAM |
| **CPU Load** | 0% — Pi 5 untouched | 100% of 1 core during inference |
| **Latency** | ~1-3s per query | ~5-15s per query |
| **Model** | Hailo-optimized VLM (proprietary) | Qwen2.5-VL (open, Apache 2.0) |
| **Accuracy** | Good for general scene description | Better for detailed analysis |
| **RAM Usage** | 0 bytes of system RAM | 3.2GB model + 1.5GB OS overhead |
| **Concurrent** | Runs alongside YOLO without impact | Slows voice if running simultaneously |
| **Setup Effort** | `sudo apt install` + `hailo-apps` | `ollama pull qwen2.5vl:3b` |
| **ML Expertise** | Zero — pre-built binary | Minimal — standard Ollama |
| **Offline** | Fully offline | Fully offline |
| **Customizable** | No (pre-compiled .hef) | Yes (fine-tuning, custom prompts) |
| **Cost** | Included with Hailo-10H hardware | Free + model download |
| **Integration** | Subprocess call to Hailo VLM binary | Ollama API (localhost:11434) |

### Implementation: Path A (Hailo-10H Native VLM)

```python
# vision_service/vlm_engine.py
import subprocess, json

class HailoVLMEngine:
    def __init__(self):
        self.cmd = [
            "python", "-m",
            "hailo_apps.python.gen_ai_apps.vlm_chat.vlm_chat",
            "--input", "rpi",
            "--format", "json"
        ]

    def query(self, image_path: str, prompt: str) -> str:
        result = subprocess.run(
            self.cmd + ["--image", image_path, "--prompt", prompt],
            capture_output=True, text=True, timeout=30
        )
        return json.loads(result.stdout)["description"]
```

### Implementation: Path B (Qwen2.5-VL on CPU)

```python
# vision_service/vlm_engine.py
import ollama

class QwenVLMEngine:
    def __init__(self):
        self.model = "qwen2.5vl:3b"

    def query(self, image_path: str, prompt: str) -> str:
        response = ollama.chat(
            model=self.model,
            messages=[{
                "role": "user",
                "content": prompt,
                "images": [image_path]
            }]
        )
        return response["message"]["content"]
```

### Configurable Selection

```python
# vision_service/vlm_engine.py
class VLMFactory:
    @staticmethod
    def create(mode: str):
        if mode == "hailo":
            return HailoVLMEngine()
        elif mode == "qwen-cpu":
            return QwenVLMEngine()
        else:
            raise ValueError(f"Unknown VLM mode: {mode}")

# Usage in main.py
from config import config
vlm_engine = VLMFactory.create(config.vlm.mode)
```

---

## 9. Hailo-10H Setup Guide

### Step 1: Enable PCIe Gen 3

```bash
# Method 1: config.txt
echo "dtparam=pciex1_gen=3" | sudo tee -a /boot/firmware/config.txt
sudo reboot

# Method 2: raspi-config
sudo raspi-config  # → Advanced Options → PCIe Speed → Enable
sudo reboot

# Verify
sudo lspci -vv | grep -A5 "Hailo"
# → Should show PCIe Gen 3 link speed
```

### Step 2: Install Hailo Drivers & Runtime

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install dkms
sudo apt install hailo-h10-all
sudo reboot
```

### Step 3: Verify Hardware Detection

```bash
hailortcli fw-control identify

# Expected output:
#   Executing on device: 0001:01:00.0
#   Device Architecture: HAILO10H
#   Firmware Version: 5.x.x
```

### Step 4: Install Hailo Apps (Vision + GenAI Demos)

```bash
git clone https://github.com/hailo-ai/hailo-apps.git
cd hailo-apps
sudo ./install.sh
source setup_env.sh
```

### Step 5: Install & Test Camera

```bash
sudo apt install rpicam-apps

# Test basic camera
rpicam-hello -t 2000

# Test YOLO detection via Hailo pipeline
rpicam-hello -t 0 --post-process-file \
  /usr/share/rpi-camera-assets/hailo_yolov8_inference.json
```

### Step 6: Install Ollama (for local LLM on Hailo)

```bash
# The hailo-apps includes hailo-ollama integration
# Or install Ollama separately:
curl -fsSL https://ollama.com/install.sh | sh

# Pull Qwen for Hailo-10H:
ollama pull qwen2.5:1.5b

# Test:
ollama run qwen2.5:1.5b "Hello, what can you see?"
```

### Step 7: Run VLM Chat Demo

```bash
# With Raspberry Pi camera
python -m hailo_apps.python.gen_ai_apps.vlm_chat.vlm_chat --input rpi

# With USB camera
python -m hailo_apps.python.gen_ai_apps.vlm_chat.vlm_chat --input usb
```

---

## 10. Implementation Roadmap

### Phase 1: Foundation (Week 1)

| Task | Details | Owner |
|------|---------|-------|
| OS setup | Raspberry Pi OS Bookworm 64-bit on NVMe | TBD |
| Enable PCIe Gen 3 | `/boot/firmware/config.txt`: `dtparam=pciex1_gen=3` | TBD |
| Install Hailo stack | `hailo-h10-all` + HailoRT verification | TBD |
| Clone `hailo-apps` | `git clone` + `install.sh` + `setup_env.sh` | TBD |
| Test camera | `rpicam-hello` + YOLO detection pipeline | TBD |
| Install MQTT | `sudo apt install mosquitto mosquitto-clients` | TBD |
| Create directory structure | All 5 service directories with placeholder `main.py` | TBD |

**Deliverable:** Pi 5 boots, Hailo-10H identified, YOLO detection running, MQTT broker active.

### Phase 2: LLM Orchestrator (Week 2)

| Task | Details | Owner |
|------|---------|-------|
| `deepseek_client.py` | OpenAI-compatible client, tool definitions | TBD |
| `ollama_client.py` | Ollama client for Qwen on Hailo-10H | TBD |
| `tool_definitions.py` | All 5 tool schemas | TBD |
| `tool_handlers/` | Stub handlers that log tool calls | TBD |
| `router.py` | Online/offline detection logic | TBD |
| Test: DeepSeek + tools | Single MQTT message → DeepSeek → tool calls → log | TBD |
| Test: Qwen + tools | Kill network → same flow via Ollama | TBD |

**Deliverable:** MQTT → LLM (auto-routed) → tool dispatch working end-to-end.

### Phase 3: Voice (Week 3)

| Task | Details | Owner |
|------|---------|-------|
| Install voice stack | `openwakeword`, `faster-whisper`, `piper-tts` | TBD |
| `wake_detector.py` | Always-on wake word, circular audio buffer | TBD |
| `stt_engine.py` | faster-whisper tiny + Vosk fallback | TBD |
| `tts_engine.py` | Piper TTS synthesis and playback | TBD |
| Voice → MQTT | Wake → STT → publish `command/in` | TBD |
| MQTT → TTS | Subscribe `response/out` → Piper playback | TBD |
| Voice interrupt | Interrupt flag, stop TTS on new wake word | TBD |

**Deliverable:** Wake word → question → TTS response loop functional.

### Phase 4: Vision (Week 4-5)

| Task | Details | Owner |
|------|---------|-------|
| `detection_pipeline.py` | Continuous YOLO on Hailo-10H + motion-based dynamic FPS/resolution | TBD |
| `shared_buffer.py` | Thread-safe detection storage (includes motion profile) | TBD |
| `vlm_engine.py` | Path A (Hailo VLM) implementation | TBD |
| `vlm_engine.py` | Path B (Qwen2.5-VL) implementation | TBD |
| `visual_detect` handler | Read shared_buffer → return to LLM | TBD |
| `vlm_query` handler | Capture frame → run VLM → return to LLM | TBD |
| Test: voice → "what do you see?" | End-to-end with VLM response | TBD |
| Test: dynamic profile switch | Wave hand in front of camera → verify switch to 640×320 @ 60fps | TBD |

**Deliverable:** Camera → YOLO (30 FPS) + VLM (on demand) → LLM tool calling.

### Phase 5: GPIO (Week 5-6)

| Task | Details | Owner |
|------|---------|-------|
| `pin_config.py` | Logical → physical pin mapping | TBD |
| `servo_controller.py` | Hardware PWM via pigpio | TBD |
| `screen_driver.py` | Abstract interface + one implementation | TBD |
| `servo_write` handler | MQTT → PWM → servo movement | TBD |
| `gpio_write` handler | MQTT → GPIO pin toggle | TBD |
| `screen_display` handler | MQTT → text on screen | TBD |
| Test servos | Physical movement via voice command | TBD |
| Test screen | Display LLM responses on-screen | TBD |

**Deliverable:** Servos respond to voice commands, screen shows LLM responses.

### Phase 6: Integration & Polish (Week 6-7)

| Task | Details | Owner |
|------|---------|-------|
| Session Manager | SQLite-backed conversation history | TBD |
| Full E2E test | "Move servo 1 to 90°, what's in front of me?" → full pipeline | TBD |
| Offline fallback test | Disconnect network, confirm Qwen takes over seamlessly | TBD |
| Thermal test | Sustained load with Active Cooler, monitor throttling | TBD |
| systemd services | All 5 services auto-start on boot | TBD |
| Health monitoring | MQTT `/system/health` heartbeats from each service | TBD |
| Documentation | README, architecture diagram, API docs | TBD |

**Deliverable:** Production-ready assistant, all 5 services as systemd units.

---

## 11. Full Data Flow Example

**User says:** *"Move servo 1 to 90 degrees and tell me what's on the screen"*

```
Step  | Component               | Action
──────┼─────────────────────────┼────────────────────────────────────────────
  1   | User                    | Speaks wake word + command
  2   | Voice: WakeDetector     | Detects wake word, starts audio capture
  3   | Voice: STT              | Transcribes: "Move servo 1 to 90 degrees 
      |                         | and tell me what's on the screen"
  4   | Voice: Publisher        | Publishes to MQTT topic: command/in
      |                         | Payload: {"text": "...", "session_id": "s1"}
  5   | Session Manager         | Creates session "s1" in SQLite
      |                         | Publishes session/active
  6   | LLM Orchestrator        | Receives command/in
  7   | LLM: Router             | Checks connectivity → online
  8   | LLM: DeepSeek           | Calls DeepSeek V4:
      |                         |   messages=[system_prompt, user_query]
      |                         |   tools=[visual_detect, vlm_query, servo_write, ...]
  9   | DeepSeek API            | Returns:
      |                         |   tool_calls=[
      |                         |     servo_write(servo=1, angle=90),
      |                         |     visual_detect()
      |                         |   ]
 10   | LLM: Tool Dispatcher    | Routes servo_write to GPIO tool handler
 11   | LLM: Tool Dispatcher    | Routes visual_detect to Vision tool handler
 12a  | GPIO: servo_write       | Publishes to gpio/command:
      |                         |   {"servo_1": 90, "session_id": "s1"}
 12b  | Vision: visual_detect   | Reads shared_buffer.get()
      |                         | Returns instantly: {"detections": [...]}
 13   | GPIO Service            | Receives gpio/command
      |                         | Calls ServoController.set_angle(1, 90)
      |                         | PWM signal on GPIO 12
      |                         | Publishes gpio/status: {"servo_1": 90, "status": "ok"}
 14   | LLM: Tool Dispatcher    | Receives visual_detect results:
      |                         |   [{"class": "person", "conf": 0.95}, 
      |                         |    {"class": "whiteboard", "conf": 0.89}]
 15   | LLM: DeepSeek           | Sends tool results back to DeepSeek
 16   | DeepSeek API            | Returns text: "Servo 1 moved to 90 degrees.
      |                         | The camera shows a person standing in front of
      |                         | a whiteboard with some writing on it."
 17   | LLM: Publisher          | Publishes response/out:
      |                         |   {"text": "Servo 1 moved to...", "session_id": "s1"}
 18   | Voice: TTS              | Receives response/out
      |                         | Piper synthesizes speech
      |                         | Plays through speaker
 19   | Session Manager         | Stores conversation turn in SQLite
      |                         | Updates session "s1" timestamp

Total latency (estimated): ~2-4 seconds with DeepSeek, ~8-15 seconds offline.
```

---

## 12. Team Discussion Questions

### Q1: VLM Strategy — Path A, Path B, or Both?

| Option | Pros | Cons |
|--------|------|------|
| **Path A only** (Hailo VLM) | Fastest, zero CPU impact, simplest setup | Not Qwen-family, closed model |
| **Path B only** (Qwen2.5-VL) | Full Qwen ecosystem, open weights, fine-tunable | 5-15s latency, uses 3.2GB RAM, blocks CPU |
| **Both (Hybrid)** | Hailo for speed, Qwen for detail | More code, more complexity |

> **Decision needed:** Start with one? Both? What criteria for switching?

### Q2: Screen Type

| Option | Interface | GPIO Pins | Complexity |
|--------|-----------|-----------|------------|
| I2C OLED (SSD1306, 128×64) | 2 (SDA + SCL) | Minimal | Low |
| SPI TFT (ILI9341, 320×240) | 5 (MOSI, MISO, SCLK, CS, DC) | Moderate | Medium |
| HDMI Monitor | HDMI 0 | None | Low (but bulky) |
| Character LCD (HD44780, 16×2) | 6 (or 4 via I2C backpack) | Moderate | Low |

### Q3: Inter-Service Communication

| Protocol | Pros | Cons |
|----------|------|------|
| **MQTT** (Mosquitto) | IoT standard, pub/sub, low overhead, loggable | Requires broker, string-based payloads |
| **gRPC** | Typed contracts (protobuf), bidirectional streams | Heavier, more boilerplate, less IoT-native |
| **Redis Pub/Sub** | Fast, in-memory, simple | Not persistent, extra dependency |
| **Raw ZeroMQ** | Fastest, no broker | No persistence, more manual wiring |

> **Recommendation:** MQTT is the safest choice for this project. gRPC could replace it later if performance demands it.

### Q4: Session Definition

| Model | Description |
|-------|-------------|
| **Single turn** | Each command is a fresh session — no memory of previous commands |
| **Conversation thread** | Session persists for N minutes, LLM sees full conversation history |
| **Hybrid** | New wake word starts new turn; within a session, follow-up questions keep context |

> **Recommendation:** Start with **single turn** (simplest). Add conversation threading in Phase 6 once the basics work.

### Q5: Servo Power

| Power Source | Max Servos | Risk | Recommendation |
|-------------|------------|------|----------------|
| Pi 5 5V rail | 1 micro servo | Brownouts, reboot with 2+ servos | Not recommended |
| External 5V PSU | Unlimited | None | **Strongly recommended** |
| External 6V PSU | Unlimited (higher torque) | Need voltage regulator for Pi | Best for larger servos |

> **Recommendation:** Use an external 5V power supply for the servos. Connect servo grounds to Pi ground. Never power more than 1 micro servo from the Pi's 5V pin.

### Q6: Enclosure & Cooling

- Hailo-10H generates significant heat under sustained LLM load
- Pi 5 throttles after ~4 minutes without cooling
- **Must have:** Active Cooler on Pi 5, heatsink on Hailo-10H
- **Nice to have:** Enclosure with fan cutout, ventilation for camera
- Consider: USB ports accessible for mic/speaker, camera mount position

---

## 13. Quick Start / Setup Guide

> **Status:** Updated 2026-07-01 — includes Hailo NPU proxy, Pi 5 GPIO (kernel PWM), and LLM backend configuration.

### 13.1 System Dependencies

```bash
# Core system packages
sudo apt update && sudo apt install -y \
  mosquitto mosquitto-clients \
  python3 python3-pip python3-venv \
  python3-gpiozero python3-lgpio \
  python3-yaml

# Ollama (CPU inference)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b
ollama serve  # Starts Ollama on port 11434

# Hailo-10H drivers (already installed via apt, verify with):
hailortcli fw-control identify
# Expected: Firmware Version: 5.1.1, Device Architecture: HAILO10H
```

### 13.2 Python Virtual Environment

```bash
cd ~/Desktop/MyProject/Pi5_AI_Camera
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install openai pyyaml paho-mqtt requests
```

> **Important:** Use `--system-site-packages` so the venv can access system-installed `hailo_platform`, `gpiozero`, and `lgpio`.

### 13.3 hailo-apps (Required for HailoClient / NPU Inference)

```bash
cd ~
git clone https://github.com/hailo-ai/hailo-apps.git
cd hailo-apps
python3 -m venv venv_hailo_apps --system-site-packages
source venv_hailo_apps/bin/activate
pip install -e .

# Verify the model is available:
ls /usr/local/hailo/resources/models/hailo10h/Qwen2.5-1.5B-Instruct.hef
```

### 13.4 Environment Variables

```bash
# Add to ~/.bashrc
export DEEPSEEK_API_KEY="sk-..."
export PYTHONPATH="$HOME:/home/userpi/hailo-apps:$PYTHONPATH"
```

### 13.5 Start All Services

#### Mosquitto (MQTT Broker)

```bash
sudo systemctl enable mosquitto
sudo systemctl start mosquitto
```

#### Hailo NPU Ollama Proxy (Port 8000)

```bash
# Copy the systemd service file (already in repo):
sudo cp pi5_assistant/llm_orchestrator/hailo-ollama-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable hailo-ollama-proxy
sudo systemctl start hailo-ollama-proxy

# Verify:
curl -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5:1.5b","messages":[{"role":"user","content":"Hi"}],"stream":false}'
```

#### Individual Services

```bash
# Each in a separate terminal:
cd ~/Desktop/MyProject/Pi5_AI_Camera
source .venv/bin/activate

# Voice Service
python3 pi5_assistant/voice_service/main.py &

# Vision Service
python3 pi5_assistant/vision_service/main.py &

# LLM Orchestrator
python3 pi5_assistant/llm_orchestrator/main.py &

# GPIO Service
python3 pi5_assistant/gpio_service/main.py &

# Session Manager
python3 pi5_assistant/session_manager/main.py &
```

### 13.6 Servo Wiring Reference

| Servo | Wire Color | Pi 5 Pin | BCM GPIO | PWM Channel |
|-------|------------|----------|----------|-------------|
| Servo 1 | Red (VCC) | Pin 2 (5V) | — | — |
|  | Black/Brown (GND) | Pin 6 (GND) | — | — |
|  | Yellow/Orange (Signal) | Pin 12 | GPIO 18 | pwm2 |
| Servo 2 | Red (VCC) | Pin 4 (5V) | — | — |
|  | Black/Brown (GND) | Pin 39 (GND) | — | — |
|  | Yellow/Orange (Signal) | Pin 32 | GPIO 12 | pwm0 |

> **Note:** For production use, power servos from an external 5V PSU, not from Pi 5V pins.

### 13.7 Test Each LLM Backend

#### DeepSeek (Online)

```bash
cd ~/Desktop/MyProject/Pi5_AI_Camera && source .venv/bin/activate
python3 << 'EOF'
import sys; sys.path.insert(0, "pi5_assistant")
from llm_orchestrator.deepseek_client import DeepSeekClient
c = DeepSeekClient({"deepseek": {"api_key_env": "DEEPSEEK_API_KEY", "model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com", "timeout": 30}})
print(c.get_text(c.chat([{"role": "user", "content": "Say hello"}])))
EOF
```

#### Hailo NPU (Native, via hailo_platform.genai.LLM)

```bash
source ~/hailo-apps/venv_hailo_apps/bin/activate
python3 << 'EOF'
import sys; sys.path.insert(0, "$HOME/Desktop/MyProject/Pi5_AI_Camera/pi5_assistant")
import site; site.addsitedir("$HOME/hailo-apps/venv_hailo_apps/lib/python3.13/site-packages")
from llm_orchestrator.hailo_client import HailoClient
c = HailoClient({"hailo": {"hef_path": "/usr/local/hailo/resources/models/hailo10h/Qwen2.5-1.5B-Instruct.hef", "max_tokens": 100, "temperature": 0.1}})
print(c.get_text(c.chat([{"role": "user", "content": "Say hello"}])))
c.stop()
EOF
```

#### Ollama via NPU Proxy (Port 8000, NPU for chat, CPU for tools)

```bash
cd ~/Desktop/MyProject/Pi5_AI_Camera && source .venv/bin/activate
python3 << 'EOF'
import sys; sys.path.insert(0, "pi5_assistant")
from llm_orchestrator.ollama_client import OllamaClient
import yaml
with open("pi5_assistant/llm_orchestrator/config.yaml") as f:
    cfg = yaml.safe_load(f)
c = OllamaClient(cfg)
print(c.get_text(c.chat([{"role": "user", "content": "Say hello"}])))
EOF
```

### 13.8 Test Servos

```bash
cd ~/Desktop/MyProject/Pi5_AI_Camera && source .venv/bin/activate
python3 << 'EOF'
import sys; sys.path.insert(0, "pi5_assistant")
import yaml
with open("pi5_assistant/gpio_service/config.yaml") as f:
    cfg = yaml.safe_load(f)
from gpio_service.servo_controller import ServoController
import time
sc = ServoController(cfg)
sc.set_angle(1, 0); sc.set_angle(2, 0); time.sleep(1)
sc.set_angle(1, 180); sc.set_angle(2, 180); time.sleep(1)
sc.set_angle(1, 90); sc.set_angle(2, 90)
sc.cleanup()
EOF
```

### 13.9 Test Full Pipeline (Voice → LLM → Response)

```bash
# Simulate a voice command via MQTT:
mosquitto_pub -t 'command/in' -m '{"text":"你好，介绍一下你自己","session_id":"test"}'

# Watch the response:
mosquitto_sub -t 'response/out'
```

### 13.10 LLM Backend Selection Logic

```
Router (router.py):
  prefer_online=true & internet available  → DeepSeek V4 Flash (cloud)
  prefer_online=false & HEF path configured → HailoClient (NPU native, no tools)
  prefer_online=false & proxy reachable    → OllamaClient → NPU proxy :8000
                                              ├─ Plain chat → NPU
                                              └─ Tool calls → CPU Ollama :11434
  fallback                                 → OllamaClient → CPU Ollama :11434
```

| Backend | Inference | CPU | Tools | Speed | Network |
|---------|----------|-----|-------|-------|---------|
| DeepSeek | Cloud GPU | 0% | ✅ | Fast | Required |
| HailoClient (NPU) | Hailo-10H | 0% | ❌ | Medium | None |
| Ollama → NPU Proxy | Hailo-10H / CPU | Low | ✅ | Medium | None |
| Ollama → CPU | Pi 5 CPU | High | ✅ | Slow | None |
