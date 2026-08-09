# Two-axis FS90 visual PID control

This service adds closed-loop pan/tilt tracking to the two-servo camera mount.
It is intended for the [two-axis SG90/FS90 gimbal design](https://www.thingiverse.com/thing:2892903).

The feedback signal is the tracked object's position in the camera image. A
stock FS90 does not expose its internal position sensor, so this is a visual
outer loop rather than direct joint-angle feedback.

## Data flow

```text
Vision service (continuous detections)
    -> vision/detections
    -> Control service (pan PID + tilt PID)
    -> gpio/command
    -> GPIO service (50 Hz hardware PWM)
    -> FS90 pan and tilt servos
```

The control service subscribes to the continuous `vision/detections` stream,
not the on-demand `vision/detect_result` response used by the LLM tools.

## PID behaviour

- The setpoint is the image centre `(0.5, 0.5)`.
- PID output is angular velocity in degrees/second and is integrated using the
  measured frame interval. This keeps motion comparable at 30 and 60 FPS.
- A centre deadband prevents visible servo chatter.
- Integral clamping and conditional integration prevent wind-up.
- The derivative term is low-pass filtered and does not kick on acquisition.
- Output-rate, time-step, command-rate, and mechanical-angle limits protect the
  mount from large or stale commands.
- Pan and tilt directions can be reversed independently in configuration.

## Detection input

`vision/detections` carries pixel boxes and the source dimensions:

```json
{
  "frame_size": {"width": 640, "height": 320},
  "coordinate_space": "pixels",
  "detections": [
    {"name": "person", "confidence": 0.90, "bbox": [300, 80, 80, 120]}
  ]
}
```

Normalised 0-1 boxes remain supported. Invalid or empty boxes are ignored.

## Configuration

Edit `control_service/config.yaml`:

- `target_labels`: classes to track; an empty list tracks the most confident
  supported detection.
- `kp`, `ki`, `kd`: independent gains for each axis.
- `output_limit`: maximum commanded angular velocity in degrees/second.
- `deadband`: accepted centre error as a fraction of image width or height.
- `min_angle`, `max_angle`, `center_angle`: safe mount travel.
- `direction`: use `1` or `-1` to match each servo's mounting orientation.

Keep the angle limits in `gpio_service/config.yaml` consistent. The GPIO
limits are the final hardware safety clamp.

## Safe first-time tuning

1. Power the servos from a suitable external 5 V supply and join its ground to
   Raspberry Pi ground. Do not drive two loaded servos from the Pi 5 V pin.
2. Start with the camera mount unloaded or ready to disconnect. Verify that
   `center_angle` and the min/max angles do not force either linkage.
3. Put `ki: 0` and `kd: 0`. Point a target slightly away from centre and check
   that both axes move toward it. Change that axis's `direction` to `-1` if it
   moves away.
4. Increase `kp` until tracking is responsive but begins to oscillate, then
   reduce it by roughly 20-30%.
5. Increase `kd` gradually to damp overshoot. Increase `deadband` if the FS90s
   buzz around centre.
6. Add only a small `ki` if a steady offset remains. Excess integral gain is a
   common cause of slow oscillation.

The supplied gains are conservative starting values, not final calibration for
every printed linkage, camera mass, or power supply.

## Run and test

The standard launcher now starts this service:

```bash
./pi5_assistant/run_all.sh
```

Run the hardware-independent tests from the repository root:

```bash
python -m unittest discover -s tests -v
```

For true joint-angle PID, stall detection, or load compensation, add external
angle sensors/encoders (or a servo that exposes feedback) and use those
measurements as the inner-loop feedback signal.
