"""
control_loop.py  -  Role 4 control logic (UNCHANGED from the standalone version)
================================================================
ControlPolicy : detections -> tracking decision + a target for the gimbal
PID / Gimbal  : keep the target centred (angles emitted by the service over MQTT)
Detection     : the input data contract (also lives here so the service imports cleanly)
"""

from dataclasses import dataclass

CONFIDENCE_THRESHOLD = 0.65
ROI_SMOOTHING = 0.4
TARGET_LABELS = None
ROI_ZOOM = 2.5
ROI_MIN = 0.35
IDLE_FPS = 10
ACTIVE_FPS = 30


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


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

    @property
    def center(self):
        return (self.x + self.w / 2, self.y + self.h / 2)


@dataclass
class SensorSettings:
    roi: tuple
    framerate: int
    resolution: tuple
    note: str = ""


IDLE = SensorSettings((0, 0, 1, 1), IDLE_FPS, (640, 480), "idle")


class PID:
    def __init__(self, kp, ki, kd, output_limit, integral_limit=1.0):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.output_limit = output_limit
        self.integral_limit = integral_limit
        self.prev_error = 0.0
        self.integral = 0.0

    def update(self, error, dt):
        if dt <= 0:
            dt = 1e-3
        self.integral = clamp(self.integral + error * dt,
                              -self.integral_limit, self.integral_limit)
        derivative = (error - self.prev_error) / dt
        out = self.kp * error + self.ki * self.integral + self.kd * derivative
        self.prev_error = error
        return clamp(out, -self.output_limit, self.output_limit)

    def reset(self):
        self.prev_error = 0.0
        self.integral = 0.0


class GimbalController:
    def __init__(self, servo=None):
        self.servo = servo
        self.pan = PID(40.0, 0.2, 8.0, 15.0)
        self.tilt = PID(40.0, 0.2, 8.0, 15.0)
        self.pan_angle = 90.0
        self.tilt_angle = 90.0

    def track(self, center, dt):
        cx, cy = center
        self.pan_angle = clamp(self.pan_angle + self.pan.update(cx - 0.5, dt), 0, 180)
        self.tilt_angle = clamp(self.tilt_angle + self.tilt.update(cy - 0.5, dt), 0, 180)
        if self.servo:
            self.servo.set_angle("pan", self.pan_angle)
            self.servo.set_angle("tilt", self.tilt_angle)
        return round(self.pan_angle, 1), round(self.tilt_angle, 1)

    def hold(self):
        self.pan.reset()
        self.tilt.reset()
        return round(self.pan_angle, 1), round(self.tilt_angle, 1)


class ControlPolicy:
    def __init__(self, min_switch_interval=0.15, empty_frames_to_idle=3):
        self.mode = "idle"
        self.last_switch = 0.0
        self.min_switch_interval = min_switch_interval
        self.empty_streak = 0
        self.empty_frames_to_idle = empty_frames_to_idle
        self.smoothed_roi = None

    def _smooth(self, roi):
        if self.smoothed_roi is None:
            self.smoothed_roi = roi
        else:
            a = ROI_SMOOTHING
            self.smoothed_roi = tuple(a * n + (1 - a) * o
                                      for n, o in zip(roi, self.smoothed_roi))
        return tuple(round(v, 3) for v in self.smoothed_roi)

    def decide(self, detections, now):
        strong = [d for d in detections if d.confidence >= CONFIDENCE_THRESHOLD]
        if TARGET_LABELS:
            strong = [d for d in strong if d.label in TARGET_LABELS]

        self.empty_streak = 0 if strong else self.empty_streak + 1
        can_switch = (now - self.last_switch) >= self.min_switch_interval

        if strong and self.mode == "idle" and can_switch:
            self.mode = "tracking"
            self.last_switch = now
        elif not strong and self.mode == "tracking":
            if self.empty_streak >= self.empty_frames_to_idle and can_switch:
                self.mode = "idle"
                self.last_switch = now
                self.smoothed_roi = None

        if self.mode == "tracking" and strong:
            t = max(strong, key=lambda d: d.confidence)
            roi = self._smooth(padded_roi(t.x, t.y, t.w, t.h))
            return (SensorSettings(roi, ACTIVE_FPS, (1920, 1080),
                                   f"tracking {t.label} ({t.confidence:.2f})"),
                    t.center, "tracking")
        if self.mode == "tracking":
            roi = self.smoothed_roi or (0, 0, 1, 1)
            return (SensorSettings(roi, ACTIVE_FPS, (1920, 1080),
                                   "tracking (holding)"), None, "tracking")
        return IDLE, None, "idle"