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

- The ROI coordinate is the original detection-box centre
  `roi_center = (x + width/2, y + height/2)` in full-frame pixels. For the
  configured 640x640 image, the PID setpoint is `(320, 320)` (normalised to
  `(0.5, 0.5)` internally so the existing gains remain well scaled).
- PID output is angular velocity in degrees/second and is integrated using the
  measured frame interval. This keeps motion comparable at 30 and 60 FPS.
- A centre deadband prevents visible servo chatter.
- Integral clamping and conditional integration prevent wind-up.
- Integral accumulation is also frozen when a mechanical angle limit is hit.
- The derivative term is low-pass filtered and does not kick on acquisition.
- Output-rate, time-step, command-rate, and mechanical-angle limits protect the
  mount from large or stale commands.
- Pan and tilt directions can be reversed independently in configuration.
- When the ROI disappears, both axes return once to their configured
  `center_angle`; 50 Hz PWM remains enabled so the FS90s hold that position.
- A watchdog performs the same return-to-centre action if Vision stops.
- A manual/LLM servo command pauses PID briefly and reseeds the matching axis,
  so automatic tracking cannot immediately overwrite the manual move.

## Detection input

`vision/detections` carries pixel boxes and the source dimensions:

```json
{
  "frame_size": {"width": 640, "height": 640},
  "frame_center": [320.0, 320.0],
  "coordinate_space": "pixels",
  "tracking_target":
    {"name": "person", "confidence": 0.90,
     "bbox": [280, 260, 80, 120], "roi_center": [320.0, 320.0]},
  "detections": [
    {"name": "person", "confidence": 0.90, "bbox": [280, 260, 80, 120]}
  ]
}
```

`tracking_target` is selected with the same rules as the displayed ROI, so PID
cannot accidentally follow another object in the full detection list.
`roi_center` always refers to the complete source frame, not the padded ROI
JPEG. Older payloads without the centre field still work by deriving it from
`bbox`. Normalised 0-1 boxes remain supported.
Vision keeps a nearest-centre lock on the current person/face, so small
confidence changes between multiple detections do not make the gimbal jump.

## Configuration

Edit `control_service/config.yaml`:

- `target_labels`: classes to track; an empty list tracks the most confident
  supported detection.
- `kp`, `ki`, `kd`: independent gains for each axis.
- `output_limit`: maximum commanded angular velocity in degrees/second.
- `deadband`: accepted centre error as a fraction of image width or height.
- `min_angle`, `max_angle`, `center_angle`: safe mount travel.
- `direction`: use `1` or `-1` to match each servo's mounting orientation.
- `detection_timeout`: reset the loop if detector feedback stops arriving.

Keep the angle limits in `gpio_service/config.yaml` consistent. The GPIO
limits are the final hardware safety clamp.

The bundled four-class model labels a detected human as `person`; a dedicated
face HEF commonly publishes `face`. Both labels are accepted by default. If
your model uses another label, change it here and in
`vision_service/config.yaml`.

## Safe first-time tuning

1. Power the servos from a suitable external 5 V supply and join its ground to
   Raspberry Pi ground. Do not drive two loaded servos from the Pi 5 V pin.
2. Start with the camera mount unloaded or ready to disconnect. Verify that
   `center_angle` and the min/max angles do not force either linkage.
   Confirm the motors are position-type **FS90**, not continuous-rotation
   **FS90R** servos.
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
