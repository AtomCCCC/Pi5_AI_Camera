# GPIO Service

Controls physical outputs: servo motors, GPIO digital pins, and display screen.

## Files

| File | Role |
|------|------|
| `main.py` | Entry point, subscribes to `gpio/command`, dispatches by type |
| `servo_controller.py` | Hardware PWM via pigpio — set angle 0-180° on servos 1 and 2 |
| `screen_driver.py` | Screen abstraction: SSD1306 OLED (I2C) with TFT/HDMI/LCD placeholders |
| `pin_config.py` | pigpio daemon manager, digital pin read/write |
| `config.yaml` | Servo pins/pulse ranges, screen type/dimensions |

## MQTT

| Direction | Topic | Payload |
|-----------|-------|---------|
| Subscribe | `gpio/command` | `{type: "servo"|"gpio"|"screen", ...params, session_id}` |

### Command Types

**servo** — `{type: "servo", servo: 1|2, angle: 0-180}`
**gpio** — `{type: "gpio", pin: 17, value: true|false}`
**screen** — `{type: "screen", content: "Hello", clear: true}`

## Extending

- **Add servo**: add entry to `config.yaml` under `servo`, PWM pin auto-detected
- **Add screen type**: implement `_init_<type>()` in `screen_driver.py`
- **Add GPIO feature**: add new `elif cmd_type == "..."` in `main.py:_on_command`

## Hardware

- Servo 1: BCM GPIO 12 (hardware PWM via pigpio)
- Servo 2: BCM GPIO 13
- OLED: I2C address `0x3C` (configurable)
- All GPIO pins use BCM numbering
