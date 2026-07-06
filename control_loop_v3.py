"""
G3008 - Control Loop & Firmware Engineer (Role 4)  --  version 3
==============================================================================
Adds, on top of v2:
  1) PID gimbal control   - two PID loops (pan + tilt) keep the target centred.
  2) Smarter decide()     - confidence threshold + ROI smoothing (no jumps).
  3) Better latency stats  - per-stage timing + p50/p95/p99 percentiles.
  4) Real-hardware ready   - one flag switches mock <-> real; swap points marked.

Still runs on the Pi (or laptop) with NO extra hardware while USE_REAL_HARDWARE
is False. Run:  python3 control_loop_v3.py
==============================================================================
"""

import time
import csv
from dataclasses import dataclass


# Flip to True on the Pi once the HAT + camera + servos are wired in.
# While False, everything is mocked and runs anywhere.
USE_REAL_HARDWARE = False

CONFIDENCE_THRESHOLD = 0.50   # ignore detections the model isn't sure about
ROI_SMOOTHING = 0.4           # 0 = no move, 1 = snap instantly (EMA factor)


# ----------------------------------------------------------------------
# Data structures  (agree Detection + SensorSettings with Zichang + Nikhil)
# ----------------------------------------------------------------------
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


@dataclass
class ControlDecision:
    settings: SensorSettings
    target_center: tuple  # (cx, cy) to aim the gimbal at, or None when idle
    mode: str


IDLE = SensorSettings(roi=(0, 0, 1, 1), framerate=2,
                      resolution=(640, 480), note="idle - nothing detected")


def clamp(value, low, high):
    return max(low, min(high, value))


# ======================================================================
# 1. PID CONTROLLER  (the gimbal part your teammate asked about)
# ======================================================================
class PID:
    """
    Classic PID. error -> control output.
      P (kp): react to the current error       (how far off are we now)
      I (ki): react to accumulated error        (kill steady offset over time)
      D (kd): react to how fast error changes   (damping, stops overshoot)
    integral_limit provides anti-windup so the I term can't blow up.
    """
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
        output = self.kp * error + self.ki * self.integral + self.kd * derivative
        self.prev_error = error
        return clamp(output, -self.output_limit, self.output_limit)

    def reset(self):
        self.prev_error = 0.0
        self.integral = 0.0


class GimbalController:
    """
    Turns 'where is the target in the frame' into servo angles that keep it
    centred. Two independent PID loops: one for pan (left-right, x) and one
    for tilt (up-down, y). Frame centre is (0.5, 0.5).
    """
    def __init__(self, servo):
        self.servo = servo
        # Tune these three per axis on real hardware. Start gentle.
        self.pan = PID(kp=40.0, ki=2.0, kd=8.0, output_limit=15.0)
        self.tilt = PID(kp=40.0, ki=2.0, kd=8.0, output_limit=15.0)
        self.pan_angle = 90.0   # servos start centred
        self.tilt_angle = 90.0

    def track(self, target_center, dt):
        cx, cy = target_center
        # Error = how far the target is from the centre of the frame.
        error_x = cx - 0.5
        error_y = cy - 0.5
        # PID gives an angle *adjustment*; add it and clamp to servo range.
        self.pan_angle = clamp(self.pan_angle + self.pan.update(error_x, dt), 0, 180)
        self.tilt_angle = clamp(self.tilt_angle + self.tilt.update(error_y, dt), 0, 180)
        self.servo.set_angle("pan", self.pan_angle)
        self.servo.set_angle("tilt", self.tilt_angle)
        return (round(self.pan_angle, 1), round(self.tilt_angle, 1))

    def hold(self):
        # No target: stop integrating so it doesn't drift, hold position.
        self.pan.reset()
        self.tilt.reset()
        return (round(self.pan_angle, 1), round(self.tilt_angle, 1))


# ======================================================================
# 2. HARDWARE INTERFACES  (mock now; real versions are the swap points)
# ======================================================================
def build_scripted_scene():
    scene = []
    for _ in range(3):
        scene.append([])
    for i in range(10):                      # car enters, drifts right + down
        scene.append([Detection("car", 0.85, 0.10 + i * 0.06, 0.30 + i * 0.02,
                                0.25, 0.25)])
    scene.append([])                         # one-frame detector blip
    for i in range(4):
        scene.append([Detection("car", 0.80, 0.70 + i * 0.04, 0.50, 0.25, 0.25)])
    scene.append([Detection("bin", 0.35, 0.1, 0.1, 0.1, 0.1)])  # low-conf -> ignored
    for _ in range(5):
        scene.append([])
    return scene


class MockNPU:
    def __init__(self):
        self.scene = build_scripted_scene()
        self.i = 0

    def infer(self):
        dets = self.scene[self.i]
        self.i += 1
        return dets

    def frame_count(self):
        return len(self.scene)


class MockSensor:
    def apply(self, settings):
        pass   # records nothing; on real HW this is the picamera2 call


class MockServo:
    def set_angle(self, name, angle):
        pass   # on real HW this drives the actual servo


# --- Real versions: fill these in on the Pi, then set USE_REAL_HARDWARE=True ---
class RealNPU:
    def infer(self):
        # TODO: read HailoRT inference output, map each box to a Detection.
        raise NotImplementedError("Wire up HailoRT read here (Zichang's output).")
    def frame_count(self):
        return 10_000


class RealSensor:
    def apply(self, settings):
        # TODO: picam2.set_controls({"ScalerCrop": ..., "FrameRate": ...})
        raise NotImplementedError("Wire up picamera2 set_controls here (Nikhil).")


class RealServo:
    def set_angle(self, name, angle):
        # TODO: servo_controller.set_angle(channel, angle)  (Jie's gimbal)
        raise NotImplementedError("Wire up the real servo driver here.")


def build_hardware():
    if USE_REAL_HARDWARE:
        return RealNPU(), RealSensor(), RealServo()
    return MockNPU(), MockSensor(), MockServo()


# ======================================================================
# 3. CONTROL POLICY  *** YOUR CORE WORK ***  (now smarter)
# ======================================================================
class ControlPolicy:
    def __init__(self, min_switch_interval=0.15, empty_frames_to_idle=3):
        self.mode = "idle"
        self.last_switch = 0.0
        self.min_switch_interval = min_switch_interval
        self.empty_streak = 0
        self.empty_frames_to_idle = empty_frames_to_idle
        self.smoothed_roi = None   # for gliding the ROI instead of jumping

    def _smooth(self, new_roi):
        if self.smoothed_roi is None:
            self.smoothed_roi = new_roi
        else:
            a = ROI_SMOOTHING
            self.smoothed_roi = tuple(a * n + (1 - a) * o
                                      for n, o in zip(new_roi, self.smoothed_roi))
        return tuple(round(v, 3) for v in self.smoothed_roi)

    def decide(self, detections, now):
        # 1) drop detections the model isn't confident enough about
        strong = [d for d in detections if d.confidence >= CONFIDENCE_THRESHOLD]

        # 2) mode switching with anti-thrashing (cooldown + debounce)
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

        # 3) produce settings + a target for the gimbal
        if self.mode == "tracking" and strong:
            target = max(strong, key=lambda d: d.confidence)
            roi = self._smooth((target.x, target.y, target.w, target.h))
            settings = SensorSettings(roi=roi, framerate=30, resolution=(1920, 1080),
                                      note=f"tracking {target.label} ({target.confidence})")
            return ControlDecision(settings, target.center, "tracking")

        if self.mode == "tracking":   # holding through a brief blip
            roi = self.smoothed_roi or (0, 0, 1, 1)
            settings = SensorSettings(roi=roi, framerate=30, resolution=(1920, 1080),
                                      note="tracking (holding, target briefly lost)")
            return ControlDecision(settings, None, "tracking")

        return ControlDecision(IDLE, None, "idle")


# ======================================================================
# 4. THE LOOP + latency harness (per-stage timing, percentiles, CSV, plot)
# ======================================================================
def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (p / 100)
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def main():
    npu, sensor, servo = build_hardware()
    policy = ControlPolicy()
    gimbal = GimbalController(servo)

    rows = []
    last_time = time.perf_counter()
    n_frames = npu.frame_count()

    for frame_idx in range(n_frames):
        loop_now = time.perf_counter()
        dt = loop_now - last_time
        last_time = loop_now

        t0 = time.perf_counter_ns()
        detections = npu.infer()                               # NPU stage
        t1 = time.perf_counter_ns()
        decision = policy.decide(detections, now=loop_now)     # YOUR decision
        t2 = time.perf_counter_ns()
        if decision.target_center is not None:                 # gimbal (PID)
            angles = gimbal.track(decision.target_center, dt)
        else:
            angles = gimbal.hold()
        t3 = time.perf_counter_ns()
        sensor.apply(decision.settings)                        # reconfigure sensor
        t4 = time.perf_counter_ns()

        row = {
            "frame": frame_idx,
            "mode": decision.mode,
            "pan": angles[0],
            "tilt": angles[1],
            "infer_ms": round((t1 - t0) / 1e6, 4),
            "decide_ms": round((t2 - t1) / 1e6, 4),
            "gimbal_ms": round((t3 - t2) / 1e6, 4),
            "apply_ms": round((t4 - t3) / 1e6, 4),
            "total_ms": round((t4 - t0) / 1e6, 4),
            "note": decision.settings.note,
        }
        rows.append(row)
        print(f"frame {frame_idx:2d} | {row['total_ms']:6.3f} ms | "
              f"pan {angles[0]:5.1f} tilt {angles[1]:5.1f} | {decision.settings.note}")
        time.sleep(0.05)

    # --- CSV ---
    with open("latency_log.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print("\nSaved latency_log.csv")

    # --- stats: mean + percentiles (your headline metric, done properly) ---
    totals = sorted(r["total_ms"] for r in rows)
    print("--- control loop latency ---")
    print(f"mean {sum(totals) / len(totals):.3f} ms")
    print(f"p50  {percentile(totals, 50):.3f} ms")
    print(f"p95  {percentile(totals, 95):.3f} ms   (95% of loops are faster than this)")
    print(f"p99  {percentile(totals, 99):.3f} ms")
    print(f"max  {totals[-1]:.3f} ms")

    # --- plot ---
    try:
        import matplotlib
        matplotlib.use("Agg")   # no-screen safe (important over SSH)
        import matplotlib.pyplot as plt
        frames = [r["frame"] for r in rows]
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
        ax1.plot(frames, [r["total_ms"] for r in rows], marker="o")
        ax1.set_ylabel("total latency (ms)"); ax1.grid(True, alpha=0.3)
        ax1.set_title("Control loop latency + gimbal angles")
        ax2.plot(frames, [r["pan"] for r in rows], marker=".", label="pan")
        ax2.plot(frames, [r["tilt"] for r in rows], marker=".", label="tilt")
        ax2.set_xlabel("frame"); ax2.set_ylabel("servo angle (deg)")
        ax2.legend(); ax2.grid(True, alpha=0.3)
        plt.tight_layout(); plt.savefig("latency_plot.png", dpi=120)
        print("Saved latency_plot.png")
    except Exception as e:
        print(f"(plot skipped: {e})")


if __name__ == "__main__":
    main()
