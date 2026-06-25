# Vision Service

Continuous real-time camera pipeline running in a background thread.

## Files

| File | Role |
|------|------|
| `main.py` | Entry point, spawns detection background thread, MQTT loop |
| `detection_pipeline.py` | YOLOv8n inference on Hailo-10H (430+ FPS @ 640×640) |
| `vlm_engine.py` | Vision-Language Model: Hailo VLM (Path A, fast) or Qwen2.5-VL-3B on CPU (Path B) |
| `shared_buffer.py` | Thread-safe buffer storing latest frame + detections — zero-copy read by LLM |
| `config.yaml` | Camera settings, YOLO model path, VLM path selection |

## MQTT

| Direction | Topic | Payload |
|-----------|-------|---------|
| Subscribe | `vision/detect` | `{classes: [], min_confidence, session_id}` |
| Publish | `vision/detect_result` | `{detections: [{class, confidence, bbox}], session_id}` |
| Subscribe | `vision/query` | `{prompt, session_id}` |
| Publish | `vision/result` | `{description, session_id}` |

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
