# Vision Service

Continuous real-time camera pipeline running in a background thread. Provides object detection (YOLO) and scene understanding (VLM) to the LLM on demand.

## Files

| File | Role |
|------|------|
| `main.py` | Entry point, spawns detection background thread, MQTT loop |
| `detection_pipeline.py` | YOLOv8n inference on Hailo-10H (430+ FPS @ 640×640) |
| `vlm_engine.py` | Vision-Language Model: Hailo VLM (Path A, fast) or Qwen2.5-VL-3B on CPU (Path B) |
| `shared_buffer.py` | Thread-safe buffer storing latest frame + detections — zero-copy read by LLM |
| `config.yaml` | Camera settings, YOLO model path, VLM path selection |

## Processing Flow

```
┌────────────────────────────────────────────────────────────────────────────┐
│                      VISION SERVICE METHODOLOGY                             │
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────┐      │
│  │              BACKGROUND THREAD (continuous, 30+ FPS)              │      │
│  │                                                                   │      │
│  │  ┌──────────────┐    ┌──────────────────┐   ┌─────────────────┐  │      │
│  │  │ Camera       │───►│ Motion Detector  │──►│ Profile Select  │  │      │
│  │  │ Module 3     │    │ (frame differ-   │   │                 │  │      │
│  │  │ IMX708       │    │  ence)           │   │ High motion →   │  │      │
│  │  │ 30fps native │    │                  │   │   640×320 @60fps│  │      │
│  │  └──────┬───────┘    └──────────────────┘   │ Low motion  →   │  │      │
│  │         │                                    │   640×640 @30fps│  │      │
│  │         ▼                                    └────────┬────────┘  │      │
│  │  ┌──────────────────────────────────────────────────────┘          │      │
│  │  │                                                               │      │
│  │  ▼                                                               │      │
│  │  ┌──────────────────────┐       ┌─────────────────────────────┐  │      │
│  │  │ YOLOv8n on Hailo-10H │──────►│ SharedBuffer                 │  │      │
│  │  │ NPU (430+ FPS always │       │ {latest_frame, detections,  │  │      │
│  │  │ @640×640 — YOLO is   │       │  profile, motion_score,     │  │      │
│  │  │ independent of camera│       │  timestamp}                 │  │      │
│  │  │ capture resolution)   │       └────────────┬────────────────┘  │      │
│  │  └──────────────────────┘                    │                    │      │
│  └──────────────────────────────────────────────┼────────────────────┘      │
│                                                  │                          │
│                        ┌─────────────────────────┘                         │
│                        ▼                                                    │
│  ┌──────────────────────────────────────────────────────────────────┐      │
│  │              ON-DEMAND HANDLERS (MQTT-driven)                     │      │
│  │                                                                   │      │
│  │  ┌──────────────────┐      ┌──────────────────────────────┐      │      │
│  │  │ visual_detect    │      │ vlm_query                    │      │      │
│  │  │                  │      │                              │      │      │
│  │  │ Reads SharedBuf  │      │ Grabs frame from SharedBuf   │      │      │
│  │  │ Filters by class │      │ Runs VLM inference:          │      │      │
│  │  │ + confidence     │      │  Path A: Hailo VLM (fast)   │      │      │
│  │  │ Returns JSON     │      │  Path B: Qwen2.5-VL on CPU  │      │      │
│  │  └────────┬─────────┘      └──────────────┬───────────────┘      │      │
│  │           │                               │                       │      │
│  │  publish: │                      publish: │                       │      │
│  │  detect_result                      result │                       │      │
│  └───────────┼───────────────────────────────┼───────────────────────┘      │
│              ▼                               ▼                              │
│        ┌──────────────┐           ┌────────────────────┐                    │
│        │ LLM receives │           │ LLM receives       │                    │
│        │ structured   │           │ natural language   │                    │
│        │ detections   │           │ description        │                    │
│        └──────────────┘           └────────────────────┘                    │
└────────────────────────────────────────────────────────────────────────────┘
```

### Step-by-Step

1. **Background Thread** — On startup, `main.py` spawns a daemon thread that continuously captures frames from Camera Module 3 (IMX708, 30fps native).
2. **Motion Estimation** — Each frame is downscaled to 160×120 and compared to the previous frame using mean absolute difference. A sliding window of 5 frames smooths the motion score.
3. **Profile Switching** — Every 0.5s, if average motion exceeds threshold (30), the camera switches to `high_motion` profile (640×320 @ 60fps — faster capture, wider FOV). When motion subsides, it switches back to `low_motion` (640×640 @ 30fps — full quality).
4. **YOLO Inference** — Regardless of camera capture resolution, YOLO always runs at its trained 640×640 resolution. The captured frame is resized to 640×640 before inference. Hailo-10H achieves 430+ FPS on YOLOv8n.
5. **Buffer Update** — Results (bounding boxes, class names, confidence scores) + the raw frame + current motion profile are written to `shared_buffer.py` via a thread-safe lock.
6. **LLM Requests Detection** — When the LLM calls `visual_detect`, the handler reads the buffer instantly (no re-inference, ~1ms).
7. **LLM Requests VLM** — When the LLM calls `vlm_query`, the handler grabs the latest frame from the buffer and runs the VLM. Path A runs on Hailo NPU (fast, zero CPU). Path B runs Qwen2.5-VL-3B on CPU (slower fallback).
8. **Result Published** — The VLM description is published to `vision/result` for the LLM to consume as a tool response.

### Dynamic Camera Profiles

The Camera Module 3's capture resolution and framerate adapt independently from YOLO's inference resolution:

| Condition | Capture Resolution | Capture FPS | YOLO Resolution | YOLO FPS |
|-----------|-------------------|-------------|-----------------|----------|
| Low motion (idle scene) | 640×640 | 30 | 640×640 | 430+ |
| High motion (fast movement) | 640×320 | 60 | resized→640×640 | 430+ |

YOLO always receives a 640×640 frame (resized from whatever the camera captured). The dynamic adjustment only affects camera readout speed and field of view — YOLO's inference speed on the Hailo-10H is unaffected.

## MQTT

| Direction | Topic | Payload | When |
|-----------|-------|---------|------|
| Subscribe | `vision/detect` | `{classes: [], min_confidence, session_id}` | LLM requests detection |
| Publish | `vision/detect_result` | `{detections: [{class, conf, bbox}], session_id}` | ← immediately |
| Subscribe | `vision/query` | `{prompt, session_id}` | LLM requests VLM |
| Publish | `vision/result` | `{description, session_id}` | ← after VLM inference |

## Design

The detection pipeline runs **continuously** in a daemon thread:
```
Camera → YOLO → SharedBuffer (latest frame + detections)
                        ↑
               LLM reads instantly (no re-inference)
```

The VLM only runs on-demand when the LLM calls `vlm_query`. The current camera frame is grabbed from the shared buffer — no need to re-capture.

## Extending

- **Switch YOLO model**: place new `.hef` file and update `config.yaml`
- **Add VLM Path C**: add method to `vlm_engine.py` implementing the interface
- **Post-processing**: add filters in `detection_pipeline.py` before buffer write
