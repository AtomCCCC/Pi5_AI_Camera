"""Visual feedback control for the two-axis FS90 camera mount.

The controller uses the detected target's image position as feedback.  It does
not rely on internal position feedback from the hobby servos (which is not
exposed by a stock FS90).  PID output is interpreted as angular velocity, so
the behaviour remains consistent when the detector frame rate changes.
"""

from dataclasses import dataclass
import math


CONFIDENCE_THRESHOLD = 0.65
ROI_SMOOTHING = 0.4
TARGET_LABELS = None
ROI_ZOOM = 2.5
ROI_MIN = 0.35
IDLE_FPS = 10
ACTIVE_FPS = 30


def clamp(value, low, high):
    return max(low, min(high, value))


def padded_roi(x, y, w, h):
    cx, cy = x + w / 2, y + h / 2
    size = max(max(w, h) * ROI_ZOOM, ROI_MIN)
    size = min(size, 1.0)
    nx = clamp(cx - size / 2, 0.0, 1.0 - size)
    ny = clamp(cy - size / 2, 0.0, 1.0 - size)
    return (nx, ny, size, size)


@dataclass
class Detection:
    label: str
    confidence: float
    x: float
    y: float
    w: float
    h: float
    center_x: float = None
    center_y: float = None

    @property
    def center(self):
        if self.center_x is not None and self.center_y is not None:
            return self.center_x, self.center_y
        return (self.x + self.w / 2, self.y + self.h / 2)


@dataclass
class SensorSettings:
    roi: tuple
    framerate: int
    resolution: tuple
    note: str = ""


IDLE = SensorSettings((0, 0, 1, 1), IDLE_FPS, (640, 480), "idle")


def detections_from_payload(payload, default_frame_size=(640, 640)):
    """Convert a Vision MQTT payload to normalised ``Detection`` objects.

    The live Vision service publishes pixel ``[x, y, width, height]`` boxes.
    Normalised boxes are also accepted for backwards compatibility.
    Malformed boxes are ignored instead of stopping the control service.
    """

    if not isinstance(payload, dict):
        return []

    frame_size = payload.get("frame_size", default_frame_size)
    if isinstance(frame_size, dict):
        frame_width = frame_size.get("width", default_frame_size[0])
        frame_height = frame_size.get("height", default_frame_size[1])
    elif isinstance(frame_size, (list, tuple)) and len(frame_size) >= 2:
        frame_width, frame_height = frame_size[:2]
    else:
        frame_width, frame_height = default_frame_size

    try:
        frame_width = float(frame_width)
        frame_height = float(frame_height)
    except (TypeError, ValueError):
        frame_width, frame_height = map(float, default_frame_size)
    if not all(
        math.isfinite(value) and value > 0
        for value in (frame_width, frame_height)
    ):
        frame_width, frame_height = map(float, default_frame_size)

    coordinate_space = str(payload.get("coordinate_space", "auto")).lower()
    # New Vision versions publish the exact detection used for the visible ROI.
    # Prefer it so the gimbal cannot choose a different object.  Falling back to
    # the complete list keeps recorded/older payloads compatible.
    if "tracking_target" in payload:
        tracking_target = payload.get("tracking_target")
        items = [tracking_target] if isinstance(tracking_target, dict) else []
    else:
        items = payload.get("detections", [])
    if not isinstance(items, (list, tuple)):
        return []

    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        box = item.get("bbox", ())
        if not isinstance(box, (list, tuple)) or len(box) < 4:
            continue
        try:
            x, y, width, height = map(float, box[:4])
            confidence = float(item.get("confidence", item.get("conf", 0.0)))
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (x, y, width, height, confidence)):
            continue
        if width <= 0 or height <= 0:
            continue

        normalised = coordinate_space in {"normalised", "normalized", "fraction"}
        if coordinate_space == "auto":
            normalised = all(0.0 <= value <= 1.0 for value in (x, y, width, height))
        if not normalised:
            x, width = x / frame_width, width / frame_width
            y, height = y / frame_height, height / frame_height

        center_x = center_y = None
        center_present = "roi_center" in item or "center" in item
        if center_present:
            raw_center = item.get("roi_center", item.get("center"))
            if isinstance(raw_center, dict):
                raw_center = (raw_center.get("x"), raw_center.get("y"))
            if isinstance(raw_center, (list, tuple)) and len(raw_center) >= 2:
                try:
                    candidate_x, candidate_y = map(float, raw_center[:2])
                except (TypeError, ValueError):
                    candidate_x = candidate_y = None
                if (
                    candidate_x is not None
                    and all(math.isfinite(value) for value in (
                        candidate_x, candidate_y
                    ))
                ):
                    normalised_x, normalised_y = candidate_x, candidate_y
                    if not normalised:
                        normalised_x /= frame_width
                        normalised_y /= frame_height
                    # Do not turn a corrupt coordinate into a maximum travel
                    # command. If it lies outside the source frame, retain
                    # backwards-compatible bbox-centre calculation below.
                    if 0.0 <= normalised_x <= 1.0 and 0.0 <= normalised_y <= 1.0:
                        center_x, center_y = normalised_x, normalised_y

        x1 = clamp(x, 0.0, 1.0)
        y1 = clamp(y, 0.0, 1.0)
        x2 = clamp(x + width, 0.0, 1.0)
        y2 = clamp(y + height, 0.0, 1.0)
        if x2 <= x1 or y2 <= y1:
            continue

        result.append(Detection(
            label=str(item.get("name", item.get("label", item.get("class", "")))),
            confidence=confidence,
            x=x1,
            y=y1,
            w=x2 - x1,
            h=y2 - y1,
            center_x=center_x,
            center_y=center_y,
        ))
    return result


class PID:
    """PID controller with anti-windup and a filtered derivative term."""

    def __init__(
        self,
        kp,
        ki,
        kd,
        output_limit,
        integral_limit=1.0,
        derivative_filter=0.2,
    ):
        try:
            self.kp = float(kp)
            self.ki = float(ki)
            self.kd = float(kd)
            self.output_limit = float(output_limit)
            self.integral_limit = float(integral_limit)
            self.derivative_filter = float(derivative_filter)
        except (TypeError, ValueError) as exc:
            raise ValueError("PID parameters must be finite numbers") from exc
        if not all(math.isfinite(value) for value in (
            self.kp,
            self.ki,
            self.kd,
            self.output_limit,
            self.integral_limit,
            self.derivative_filter,
        )):
            raise ValueError("PID parameters must be finite numbers")
        if self.output_limit <= 0:
            raise ValueError("output_limit must be positive")
        if self.integral_limit < 0:
            raise ValueError("integral_limit cannot be negative")
        if not 0.0 <= self.derivative_filter <= 1.0:
            raise ValueError("derivative_filter must be between 0 and 1")
        self.prev_error = None
        self.integral = 0.0
        self.derivative = 0.0

    def update(self, error, dt, integrate=True):
        dt = max(float(dt), 1e-6)
        error = float(error)

        # Initialising from the first sample avoids a large derivative kick.
        raw_derivative = (
            0.0 if self.prev_error is None else (error - self.prev_error) / dt
        )
        alpha = self.derivative_filter
        self.derivative = alpha * raw_derivative + (1.0 - alpha) * self.derivative

        candidate_integral = self.integral
        if integrate:
            candidate_integral = clamp(
                self.integral + error * dt,
                -self.integral_limit,
                self.integral_limit,
            )
        candidate_output = (
            self.kp * error
            + self.ki * candidate_integral
            + self.kd * self.derivative
        )

        # Conditional integration prevents further wind-up while saturated.
        saturated_high = candidate_output > self.output_limit and error > 0
        saturated_low = candidate_output < -self.output_limit and error < 0
        if integrate and not (saturated_high or saturated_low):
            self.integral = candidate_integral

        output = (
            self.kp * error
            + self.ki * self.integral
            + self.kd * self.derivative
        )
        self.prev_error = error
        return clamp(output, -self.output_limit, self.output_limit)

    def reset(self):
        self.prev_error = None
        self.integral = 0.0
        self.derivative = 0.0


DEFAULT_AXIS_CONFIG = {
    "kp": 60.0,
    "ki": 0.0,
    "kd": 2.0,
    "output_limit": 60.0,
    "integral_limit": 0.25,
    "derivative_filter": 0.2,
    "deadband": 0.03,
    "min_angle": 20.0,
    "max_angle": 160.0,
    "center_angle": 90.0,
    "direction": 1.0,
}


class GimbalController:
    """Two independent visual PID loops for pan and tilt."""

    def __init__(
        self,
        servo=None,
        pan_config=None,
        tilt_config=None,
        dt_min=0.005,
        dt_max=0.2,
        pan_channel=1,
        tilt_channel=2,
    ):
        self.servo = servo
        self.pan_channel = pan_channel
        self.tilt_channel = tilt_channel
        self.pan_config = self._axis_config(pan_config)
        self.tilt_config = self._axis_config(tilt_config)
        self.dt_min = float(dt_min)
        self.dt_max = float(dt_max)
        if (
            not math.isfinite(self.dt_min)
            or not math.isfinite(self.dt_max)
            or self.dt_min <= 0
            or self.dt_max < self.dt_min
        ):
            raise ValueError("expected 0 < dt_min <= dt_max")

        self.pan = self._pid(self.pan_config)
        self.tilt = self._pid(self.tilt_config)
        self.pan_angle = self.pan_config["center_angle"]
        self.tilt_angle = self.tilt_config["center_angle"]

    @staticmethod
    def _axis_config(config):
        merged = dict(DEFAULT_AXIS_CONFIG)
        merged.update(config or {})
        for key in merged:
            merged[key] = float(merged[key])
        if not all(math.isfinite(value) for value in merged.values()):
            raise ValueError("servo PID configuration must contain finite numbers")
        if merged["min_angle"] >= merged["max_angle"]:
            raise ValueError("servo min_angle must be less than max_angle")
        if not merged["min_angle"] <= merged["center_angle"] <= merged["max_angle"]:
            raise ValueError("servo center_angle must be inside its angle limits")
        if merged["deadband"] < 0 or merged["deadband"] >= 0.5:
            raise ValueError("deadband must be in the range 0 <= deadband < 0.5")
        if merged["direction"] not in (-1.0, 1.0):
            raise ValueError("servo direction must be 1 or -1")
        return merged

    @staticmethod
    def _pid(config):
        return PID(
            kp=config["kp"],
            ki=config["ki"],
            kd=config["kd"],
            output_limit=config["output_limit"],
            integral_limit=config["integral_limit"],
            derivative_filter=config["derivative_filter"],
        )

    @staticmethod
    def _deadband(error, width):
        if abs(error) <= width:
            return 0.0
        return math.copysign(abs(error) - width, error)

    def _step_axis(self, error, dt, pid, angle, config):
        error = self._deadband(error, config["deadband"])
        if error == 0.0:
            pid.reset()
            return angle

        direction = config["direction"]
        signed_error = error * direction
        pushing_lower_limit = (
            angle <= config["min_angle"] and signed_error < 0.0
        )
        pushing_upper_limit = (
            angle >= config["max_angle"] and signed_error > 0.0
        )
        at_outward_limit = pushing_lower_limit or pushing_upper_limit
        if at_outward_limit:
            # The image loop cannot reduce an error by pushing farther through
            # a mechanical stop.  Clear I and keep it frozen until error reverses.
            pid.integral = 0.0
        velocity = pid.update(
            error, dt, integrate=not at_outward_limit
        ) * direction
        return clamp(
            angle + velocity * dt,
            config["min_angle"],
            config["max_angle"],
        )

    def track(self, center, dt):
        cx, cy = center
        dt = clamp(float(dt), self.dt_min, self.dt_max)
        self.pan_angle = self._step_axis(
            cx - 0.5, dt, self.pan, self.pan_angle, self.pan_config
        )
        self.tilt_angle = self._step_axis(
            cy - 0.5, dt, self.tilt, self.tilt_angle, self.tilt_config
        )
        if self.servo:
            if hasattr(self.servo, "set_angles"):
                self.servo.set_angles({
                    self.pan_channel: self.pan_angle,
                    self.tilt_channel: self.tilt_angle,
                })
            else:
                self.servo.set_angle(self.pan_channel, self.pan_angle)
                self.servo.set_angle(self.tilt_channel, self.tilt_angle)
        return round(self.pan_angle, 1), round(self.tilt_angle, 1)

    def hold(self):
        self.pan.reset()
        self.tilt.reset()
        return round(self.pan_angle, 1), round(self.tilt_angle, 1)

    def recenter(self):
        """Command both axes to their calibrated centre and keep PWM active."""
        self.pan.reset()
        self.tilt.reset()
        self.pan_angle = self.pan_config["center_angle"]
        self.tilt_angle = self.tilt_config["center_angle"]
        if self.servo:
            if hasattr(self.servo, "set_angles"):
                self.servo.set_angles({
                    self.pan_channel: self.pan_angle,
                    self.tilt_channel: self.tilt_angle,
                })
            else:
                self.servo.set_angle(self.pan_channel, self.pan_angle)
                self.servo.set_angle(self.tilt_channel, self.tilt_angle)
        return round(self.pan_angle, 1), round(self.tilt_angle, 1)


class ControlPolicy:
    def __init__(
        self,
        min_switch_interval=0.15,
        empty_frames_to_idle=3,
        confidence_threshold=CONFIDENCE_THRESHOLD,
        target_labels=TARGET_LABELS,
    ):
        self.mode = "idle"
        self.last_switch = 0.0
        self.min_switch_interval = float(min_switch_interval)
        self.empty_streak = 0
        self.empty_frames_to_idle = int(empty_frames_to_idle)
        self.confidence_threshold = float(confidence_threshold)
        self.target_labels = target_labels
        self.smoothed_roi = None

    def reset(self):
        """Forget the current target after a detector-stream timeout."""
        self.mode = "idle"
        self.last_switch = 0.0
        self.empty_streak = 0
        self.smoothed_roi = None

    def _smooth(self, roi):
        if self.smoothed_roi is None:
            self.smoothed_roi = roi
        else:
            self.smoothed_roi = tuple(
                ROI_SMOOTHING * new + (1 - ROI_SMOOTHING) * old
                for new, old in zip(roi, self.smoothed_roi)
            )
        return tuple(round(value, 3) for value in self.smoothed_roi)

    def decide(self, detections, now):
        strong = [
            detection for detection in detections
            if detection.confidence >= self.confidence_threshold
        ]
        if self.target_labels:
            strong = [
                detection for detection in strong
                if detection.label in self.target_labels
            ]

        self.empty_streak = 0 if strong else self.empty_streak + 1
        can_switch = (now - self.last_switch) >= self.min_switch_interval

        if strong and self.mode == "idle" and can_switch:
            self.mode = "tracking"
            self.last_switch = now
        elif not strong and self.mode == "tracking":
            if self.empty_streak >= self.empty_frames_to_idle:
                self.mode = "idle"
                self.last_switch = now
                self.smoothed_roi = None

        if self.mode == "tracking" and strong:
            target = max(strong, key=lambda detection: detection.confidence)
            roi = self._smooth(padded_roi(
                target.x, target.y, target.w, target.h
            ))
            return (
                SensorSettings(
                    roi,
                    ACTIVE_FPS,
                    (1920, 1080),
                    f"tracking {target.label} ({target.confidence:.2f})",
                ),
                target.center,
                "tracking",
            )
        if self.mode == "tracking":
            roi = self.smoothed_roi or (0, 0, 1, 1)
            return (
                SensorSettings(
                    roi, ACTIVE_FPS, (1920, 1080), "tracking (holding)"
                ),
                None,
                "tracking",
            )
        return IDLE, None, "idle"
