# Control Service (Role 4)

The decision layer between AI detection and the physical camera gimbal.
Takes object detections, decides what to track, and outputs servo angles.

This is the only service that performs **feedback control** — the PID loop that
keeps a target centred in the frame.

## What it does

1. Subscribes to detections published by the Vision service
2. Filters them (confidence gating) and selects a target to track
3. Runs a dual-axis PID controller to compute pan/tilt angles
4. Publishes those angles to the GPIO service, which drives the servos
5. Publishes an active/idle power hint so the Vision service can throttle
   its framerate when nothing is being tracked

## MQTT interface

### Subscribes

**`vision/detect_result`** — detections from the Vision service
```json
{
  "session_id": "abc",
  "detections": [
    { "label": "person", "confidence": 0.90, "bbox": [x, y, w, h] }
  ]
}
```
`bbox` is `[x, y, w, h]` as fractions of the frame (0–1).

### Publishes

**`gpio/command`** — one message per servo (matches the GPIO service format)
```json
{ "type": "servo", "servo": 1, "angle": 95, "session_id": "abc" }
```
`servo: 1` = pan, `servo: 2` = tilt. `angle` is 0–180 degrees.

**`control/status`** — for the dashboard
```json
{ "mode": "tracking", "note": "...", "pan": 95, "tilt": 88, "latency_ms": 0.02 }
```

**`control/power_mode`** — active/idle hint (published only on change)
```json
{ "active": true }
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | The MQTT service: subscribe, decide, publish |
| `control_loop.py` | Control policy + PID (the core logic) |
| `config.yaml` | Broker address and topic names |
| `__init__.py` | Package marker |

## Running

Requires an MQTT broker (Mosquitto) on `localhost:1883`.

```bash
python -m control_service.main
```

## Control design

- **Confidence gating** (0.65) — rejects spurious detections
- **Hysteresis** — a switching cooldown and a multi-frame dropout debounce
  prevent unstable rapid mode changes
- **ROI dilation + smoothing** — a padded, smoothed region of interest
- **PID** — dual-axis (pan, tilt) with output limiting and integral anti-windup

Measured control-loop latency (standalone characterisation, 238 frames):
mean 25.42 ms, p95 25.68 ms — of which the control logic itself is ~0.04 %,
the remainder being NPU inference.

## Notes for integration

- The Vision service must publish detections in the `bbox: [x, y, w, h]`
  (normalised) format shown above. If its format differs, only the parsing in
  `_on_detections` needs adjusting.
- Servo channels (pan = 1, tilt = 2) are set in `config.yaml` and must match
  the GPIO service's `servo1` / `servo2` pins.