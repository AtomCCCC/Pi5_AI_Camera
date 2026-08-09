# Vision Service

Continuous real-time camera pipeline running in a background thread. Provides object detection (YOLO) and scene understanding (VLM) to the LLM on demand. A lightweight KAN controller adapts the camera's physical-layer parameters (resolution / framerate) to the semantic content of the scene.

## Files

| File | Role |
|------|------|
| `main.py` | Entry point, spawns detection background thread, MQTT loop |
| `detection_pipeline.py` | YOLOv8n inference on Hailo + KAN-based profile decision |
| `student_kan_new.py` | Pure-NumPy KAN controller: `[s_id, delta_s_id] -> alpha` |
| `kan_weights.npz` | Deployed KAN weights (distilled, no torch dependency) |
| `vlm_engine.py` | Vision-Language Model: Hailo VLM (Path A) or Qwen2.5-VL-3B on CPU (Path B) |
| `shared_buffer.py` | Thread-safe buffer storing latest frame + detections — zero-copy read by LLM |
| `config.yaml` | Camera settings, native-mode profiles, YOLO/VLM path selection |

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
│  │  │ 30fps native │    │                  │   │   1536×864 @120fps│  │      │
│  │  └──────┬───────┘    └──────────────────┘   │ Low motion  →   │  │      │
│  │         │                                    │   2304×1296 @56fps│  │      │
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


1. **Background Thread** — On startup, `main.py` spawns a daemon thread that continuously captures frames from Camera Module 3 (IMX708, 30fps native).
2. **Motion Estimation** — Each frame is downscaled to 160×120 and compared to the previous frame using mean absolute difference. A sliding window of 5 frames smooths the motion score.
3. **Profile Switching** — Every 0.5s, if average motion exceeds threshold (30), the camera switches to `high_motion` profile (640×320 @ 60fps — faster capture, wider FOV). When motion subsides, it switches back to `low_motion` (640×640 @ 30fps — full quality).
4. **YOLO Inference** — Regardless of camera capture resolution, YOLO always runs at its trained 640×640 resolution. The captured frame is resized to 640×640 before inference. Hailo-10H achieves 430+ FPS on YOLOv8n.
5. **Buffer Update** — Results (bounding boxes, class names, confidence scores) + the raw frame + current motion profile are written to `shared_buffer.py` via a thread-safe lock.
6. **LLM Requests Detection** — When the LLM calls `visual_detect`, the handler reads the buffer instantly (no re-inference, ~1ms).
7. **LLM Requests VLM** — When the LLM calls `vlm_query`, the handler grabs the latest frame from the buffer and runs the VLM. Path A runs on Hailo NPU (fast, zero CPU). Path B runs Qwen2.5-VL-3B on CPU (slower fallback).
8. **Result Published** — The VLM description is published to `vision/result` for the LLM to consume as a tool response.
9. **ROI Published** — The highest-confidence detections are clipped to the source frame, padded, resized if needed, JPEG encoded, and published together on `vision/roi`. An empty `rois` list clears stale dashboard images when a frame has no detections.
## Step-by-Step

1. **Background Thread** — On startup, `main.py` spawns a daemon thread that continuously captures frames from Camera Module 3 (IMX708).

2. **Semantic Density** — For the target class (selected by the LLM), the pipeline computes `s_id = 0.7·area_ratio + 0.3·count_score` and the frame-to-frame change rate `delta_s_id`.

3. **KAN Profile Decision** — The KAN controller maps `[s_id, delta_s_id]` to a continuous control coefficient `alpha`. A hysteresis band selects the profile without flapping near the boundary:
   - `alpha > 0.6` → **high_motion** (1536×864 @ 120fps, FPS-priority)
   - `alpha < 0.4` → **low_motion** (2304×1296 @ 56fps, RES-priority)
   - `0.4 – 0.6` → hold the current profile
   These two profiles are the sensor's native modes, so switching truly reconfigures the physical layer within the sensor's bandwidth limit.

4. **YOLO Inference** — Regardless of capture resolution, YOLO runs at its trained 640×640 resolution. The captured frame is resized to 640×640 before inference.

5. **Buffer Update** — Detections + raw frame + current profile are written to `shared_buffer.py` via a thread-safe lock.

6. **LLM Requests Detection** — When the LLM calls `visual_detect`, the handler reads the buffer instantly (~1ms) and also updates the KAN's target class.

7. **LLM Requests VLM** — When the LLM calls `vlm_query`, the handler grabs the latest frame and runs the VLM.

8. **Result Published** — Results are published on MQTT for the LLM to consume.

## Dynamic Camera Profiles

The KAN adapts the camera's native capture mode to the scene, independently from YOLO's fixed inference resolution:

| Condition | Capture Resolution | Capture FPS | Priority |
|-----------|--------------------|-------------|----------|
| Dense & static scene | 2304×1296 | 56 | Resolution (see detail) |
| Fast-moving scene | 1536×864 | 120 | Framerate (catch motion) |

Both profiles are native IMX708 modes with roughly equal pixel throughput (R×F ≈ sensor bandwidth limit), so the KAN's decision is effectively an optimal allocation of a fixed bandwidth budget between spatial and temporal resolution. The continuous `alpha` also enables hysteresis and seamless extension to more tiers when more native modes are available.

## MQTT

| Direction | Topic | Payload | When |
|-----------|-------|---------|------|
| Subscribe | `vision/detect` | `{classes: [], min_confidence, session_id}` | LLM requests detection / sets target |
| Publish | `vision/detect_result` | `{detections: [{class, conf, bbox}], session_id}` | immediately |
| Subscribe | `vision/query` | `{prompt, session_id}` | LLM requests VLM |
| Publish | `vision/result` | `{description, session_id}` | ← after VLM inference |
| Publish | `vision/frame` | `{image_b64, timestamp, detections}` | Dashboard preview interval |
| Publish | `vision/roi` | `{timestamp, frame_size, rois: [...]}` | ROI interval, including empty updates |

### ROI payload

Each entry in `rois` contains the source detection `bbox` in pixel
`[x, y, width, height]` format, the padded/clipped `crop_bbox`, class metadata,
encoded image dimensions, MIME type, and a Base64 JPEG in `image_b64`.
`roi.max_regions`, `padding_ratio`, `max_dimension`, `jpeg_quality`, and
`publish_interval` are configurable in `config.yaml`.
| Publish | `vision/result` | `{description, session_id}` | after VLM inference |

## Design
The KAN controller is chip-agnostic (pure NumPy) and the target class is chosen by the LLM at runtime, so the algorithm stays general while the physical-layer profiles adapt to real semantic content.

## Extending

- **Switch YOLO model**: place the `.hef` file on the Pi, then update
  `detection.hef_path`, `detection.labels`, and `input_color_order` in
  `config.yaml` together. Startup fails with a clear path error if the HEF is
  missing.
- **Add VLM Path C**: add method to `vlm_engine.py` implementing the interface
- **Post-processing**: add filters in `detection_pipeline.py` before buffer write
- **Switch YOLO model**: place the new `.hef` file and update `detection.hef_path` in `config.yaml`.
- **Add more profile tiers**: add native modes to `config.yaml` `profiles` and map `alpha` to more bands in `select_profile`.
- **Add VLM Path C**: add a method to `vlm_engine.py` implementing the interface.
