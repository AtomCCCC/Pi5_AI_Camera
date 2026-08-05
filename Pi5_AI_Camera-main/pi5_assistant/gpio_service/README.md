# GPIO Service

Controls physical outputs: servo motors, GPIO digital pins, and display screen. All commands arrive via MQTT from the LLM Orchestrator.

## Files

| File | Role |
|------|------|
| `main.py` | Entry point, subscribes to `gpio/command`, dispatches by type |
| `servo_controller.py` | Kernel PWM via `/sys/class/pwm/pwmchip0` — set angle 0-180° on servos 1 and 2 |
| `screen_driver.py` | Screen abstraction: SSD1306 OLED (I2C) with TFT/HDMI/LCD placeholders |
| `pin_config.py` | gpiozero + lgpio for GPIO digital pin read/write (Pi 5 compatible) |
| `config.yaml` | Servo pins, screen type/dimensions |

## Processing Flow

```
┌──────────────────────────────────────────────────────────────────────────┐
│                       GPIO SERVICE METHODOLOGY                            │
│                                                                           │
│  ┌────────────────┐                                                       │
│  │ gpio/command   │  {type: "servo"|"gpio"|"screen", ...params}           │
│  │ arrives via    │                                                       │
│  │ MQTT           │                                                       │
│  └────────┬───────┘                                                       │
│           ▼                                                               │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │                     COMMAND DISPATCHER                              │   │
│  │                                                                     │   │
│  │  ┌──────────┐    ┌──────────┐    ┌──────────┐                      │   │
│  │  │ type ==  │    │ type ==  │    │ type ==  │                      │   │
│  │  │ "servo"  │    │ "gpio"   │    │ "screen" │                      │   │
│  │  └────┬─────┘    └────┬─────┘    └────┬─────┘                      │   │
│  │       ▼               ▼               ▼                             │   │
│  │  ┌──────────┐   ┌──────────┐   ┌──────────┐                        │   │
│  │  │ Servo    │   │ Pin      │   │ Screen   │                        │   │
│  │  │Controller│   │Config    │   │Driver    │                        │   │
│  │  │          │   │          │   │          │                        │   │
│  │  │ set_angle│   │ write_pin│   │ display  │                        │   │
│  │  │ (1..180°)│   │ (HIGH/   │   │ (text,   │                        │   │
│  │  │          │   │  LOW)    │   │  clear)  │                        │   │
│  │  └────┬─────┘   └────┬─────┘   └────┬─────┘                        │   │
│  └───────┼──────────────┼──────────────┼──────────────────────────────┘   │
│          ▼              ▼              ▼                                   │
│  ┌──────────────┐ ┌──────────┐ ┌──────────────┐                           │
│  │ Servo 1 (BCM │ │ GPIO pin │ │ OLED Display │                           │
│  │ 18) or       │ │ (3.3V /  │ │ via I2C      │                           │
│  │ Servo 2 (BCM │ │ 0V)      │ │ 0x3C         │                           │
│  │ 12)          │ │          │ │              │                           │
│  └──────────────┘ └──────────┘ └──────────────┘                           │
└──────────────────────────────────────────────────────────────────────────┘
```

### Step-by-Step

1. **Command Arrives** — `main.py` receives `{type, ...params, session_id}` on `gpio/command`.
2. **Dispatch** — The `type` field determines which hardware to control:
   - **`servo`** → `servo_controller.py` converts angle (0-180°) to pulse width via linear interpolation. Uses Pi 5 kernel PWM (`/sys/class/pwm/pwmchip0`): GPIO18→pwm2, GPIO12→pwm0.
   - **`gpio`** → `pin_config.py` writes `HIGH (True)` or `LOW (False)` to the specified BCM pin via gpiozero+lgpio.
   - **`screen`** → `screen_driver.py` writes text to the display. Currently supports SSD1306 OLED via I2C; TFT/HDMI/LCD are pluggable.

## MQTT

| Direction | Topic | Payload | When |
|-----------|-------|---------|------|
| Subscribe | `gpio/command` | `{type: "servo"|"gpio"|"screen", ...params, session_id}` | LLM calls a tool |

### Command Payloads

**servo** — `{type: "servo", servo: 1|2, angle: 0-180}`
**gpio** — `{type: "gpio", pin: 17, value: true|false}`
**screen** — `{type: "screen", content: "Hello", clear: true}`

## Extending

- **Add servo**: add entry to `config.yaml` under `servo`, PWM pin auto-detected
- **Add screen type**: implement `_init_<type>()` in `screen_driver.py`
- **Add GPIO feature**: add new `elif cmd_type == "..."` in `main.py:_on_command`

## Hardware

- Servo 1: BCM GPIO 18 (Pin 12) — kernel PWM pwm2
- Servo 2: BCM GPIO 12 (Pin 32) — kernel PWM pwm0
- OLED: I2C address `0x3C` (configurable)
- All GPIO pins use BCM numbering