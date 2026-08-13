# GPIO service

This service is the only process that writes the Raspberry Pi 5 hardware
outputs. It receives JSON commands on `gpio/command` and controls the two FS90
servos, digital GPIO pins, and the configured display.

## FS90 gimbal

- Servo 1 (pan): BCM GPIO 13, physical pin 33, kernel PWM channel 1.
- Servo 2 (tilt): BCM GPIO 12, physical pin 32, kernel PWM channel 0.
- PWM frequency: 50 Hz.
- Initial FS90 calibration: 900 microseconds at logical 0 degrees and 2100
  microseconds at logical 180 degrees.
- Allowed command range: 20-160 degrees, centred at 90 degrees. With the
  initial calibration this uses only about 1033-1967 microseconds.

`pulse_min_angle`/`pulse_max_angle` describe the angles used to calibrate the
pulse endpoints. `min_angle`/`max_angle` are the narrower allowed mechanical
travel. All values are starting points, not a substitute for checking the
actual linkage. The GPIO layer clamps every command even if an upstream client
sends 0 or 180 degrees.

Use a separate regulated 5 V supply for two loaded servos and connect its
ground to Raspberry Pi ground. A standard FS90 is a position servo; an FS90R
is continuous-rotation and cannot be positioned by these angle commands.

Enable both pin functions in `/boot/firmware/config.txt`, then reboot:

```ini
dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
```

After reboot, `pinctrl get 12,13` should report PWM functions. The Linux
`pwmchipN` number can vary with the installed kernel; set `pwm.chip` in
`config.yaml` to the RP1 controller that contains both `pwm0` and `pwm1`.
Do not simultaneously enable an audio/I2C overlay that assigns GPIO12/13,
such as an audio remap using pins 12/13.

## MQTT commands

One servo, retained for manual/LLM control:

```json
{"type": "servo", "servo": 1, "angle": 90}
```

Paired PID update:

```json
{"type": "gimbal", "angles": {"1": 92, "2": 88}, "source": "visual_pid"}
```

Paired updates are validated first and written serially under one lock. This
prevents an older high-frequency PID command from arriving after a newer one.
Manual single-servo commands take priority for the configured three-second
override window. During that window PID gimbal messages are ignored; the
control service is notified of the applied angle and resumes from it afterward.

Other supported messages are:

```json
{"type": "gpio", "pin": 17, "value": true}
{"type": "screen", "content": "Hello", "clear": true}
```

## Shutdown behaviour

`SIGINT` and `SIGTERM` trigger cleanup. PWM outputs are disabled and channels
exported by this process are released. ROI loss or a detector-stream timeout is
handled by the control service: it sends one paired command to the configured
centre angles and keeps hardware PWM enabled so both FS90s hold position.
