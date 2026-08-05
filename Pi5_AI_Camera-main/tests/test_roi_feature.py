"""Hardware-independent tests for ROI post-processing, MQTT payloads, and UI state."""

import base64
import json
import sys
import types
import unittest
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "pi5_assistant"
sys.path.insert(0, str(APP_ROOT))


try:
    import cv2  # noqa: F401
except ImportError:
    cv2_stub = types.ModuleType("cv2")
    cv2_stub.INTER_AREA = 3
    cv2_stub.IMWRITE_JPEG_QUALITY = 1

    def _resize(image, size, interpolation=None):
        width, height = size
        channels = image.shape[2] if image.ndim == 3 else None
        shape = (height, width, channels) if channels else (height, width)
        return np.zeros(shape, dtype=image.dtype)

    def _imencode(_extension, _image, _params=None):
        encoded = np.frombuffer(b"\xff\xd8test-jpeg\xff\xd9", dtype=np.uint8)
        return True, encoded

    cv2_stub.resize = _resize
    cv2_stub.imencode = _imencode
    sys.modules["cv2"] = cv2_stub


try:
    import flask  # noqa: F401
except ImportError:
    flask_stub = types.ModuleType("flask")
    flask_stub.Flask = object
    flask_stub.Response = object
    flask_stub.jsonify = lambda value: value
    flask_stub.render_template_string = lambda value: value
    sys.modules["flask"] = flask_stub


try:
    import paho.mqtt.client  # noqa: F401
except ImportError:
    paho_stub = types.ModuleType("paho")
    mqtt_package_stub = types.ModuleType("paho.mqtt")
    mqtt_client_stub = types.ModuleType("paho.mqtt.client")
    mqtt_client_stub.Client = object
    paho_stub.mqtt = mqtt_package_stub
    mqtt_package_stub.client = mqtt_client_stub
    sys.modules["paho"] = paho_stub
    sys.modules["paho.mqtt"] = mqtt_package_stub
    sys.modules["paho.mqtt.client"] = mqtt_client_stub


from dashboard_service.main import DashboardState  # noqa: E402
from vision_service.detection_pipeline import DetectionPipeline  # noqa: E402


JPEG_BYTES = b"\xff\xd8test-jpeg\xff\xd9"


class FakeMQTT:
    def __init__(self):
        self.messages = []

    def publish(self, topic, payload):
        self.messages.append((topic, payload))


class FakeBuffer:
    def update(self, _detections, frame=None):
        self.frame = frame


class FakeMessage:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = json.dumps(payload).encode("utf-8")


def pipeline_config():
    return {
        "camera": {
            "base_resolution": [200, 100],
            "dynamic_adjust": {
                "enabled": False,
                "check_interval": 0.5,
                "motion_threshold": 30,
                "profiles": {},
            },
        },
        "detection": {"confidence": 0.5},
        "mqtt": {
            "frame_publish_interval": 0.2,
            "topic_frame": "vision/frame",
            "topic_detections": "vision/detections",
            "topic_roi": "vision/roi",
        },
        "roi": {
            "enabled": True,
            "max_regions": 2,
            "padding_ratio": 0.1,
            "max_dimension": 80,
            "jpeg_quality": 80,
            "publish_interval": 0,
        },
    }


class DetectionPipelineROITests(unittest.TestCase):
    def setUp(self):
        self.mqtt = FakeMQTT()
        self.pipeline = DetectionPipeline(
            pipeline_config(), FakeBuffer(), self.mqtt
        )

    def test_hailo_normalized_yxyx_is_converted_to_pixel_xywh(self):
        raw = np.array(
            [1, 0.25, 0.10, 0.75, 0.60, 0.90, 0, 0, 0],
            dtype=np.float32,
        )

        detections = self.pipeline._postprocess(raw, (200, 400, 3))

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0]["name"], "person")
        self.assertEqual(detections[0]["bbox"], [40, 50, 200, 100])

    def test_default_four_class_labels_follow_coco_order(self):
        raw = np.array(
            [0, 1, 0.10, 0.20, 0.30, 0.40, 0.80, 0, 0],
            dtype=np.float32,
        )

        detections = self.pipeline._postprocess(raw, (100, 200, 3))

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0]["class"], 1)
        self.assertEqual(detections[0]["name"], "bicycle")

    def test_roi_payload_is_ranked_padded_resized_and_base64_encoded(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        detections = [
            {
                "class": 1,
                "name": "car",
                "confidence": 0.60,
                "bbox": [190, 90, 30, 20],
            },
            {
                "class": 0,
                "name": "person",
                "confidence": 0.95,
                "bbox": [50, 20, 80, 60],
            },
        ]

        self.pipeline._publish_roi_images(frame, detections, 123.5)

        topic, payload = self.mqtt.messages[-1]
        self.assertEqual(topic, "vision/roi")
        self.assertEqual(payload["timestamp"], 123.5)
        self.assertEqual(payload["frame_size"], {"width": 200, "height": 100})
        json.dumps(payload)
        self.assertEqual([roi["name"] for roi in payload["rois"]], ["person", "car"])

        first = payload["rois"][0]
        self.assertEqual(first["crop_bbox"], [42, 14, 96, 72])
        self.assertEqual(first["image_size"], {"width": 80, "height": 60})
        self.assertTrue(
            base64.b64decode(first["image_b64"]).startswith(b"\xff\xd8")
        )

        second = payload["rois"][1]
        self.assertEqual(second["crop_bbox"], [187, 88, 13, 12])

    def test_empty_detection_update_clears_remote_rois(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)

        self.pipeline._publish_roi_images(frame, [], 124.0)

        topic, payload = self.mqtt.messages[-1]
        self.assertEqual(topic, "vision/roi")
        self.assertEqual(payload["rois"], [])

    def test_published_payload_is_consumed_by_dashboard_state(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        detection = {
            "class": 0,
            "name": "person",
            "confidence": 0.93,
            "bbox": [20, 10, 40, 50],
        }
        self.pipeline._publish_roi_images(frame, [detection], 125.0)
        topic, payload = self.mqtt.messages[-1]

        dashboard = DashboardState(roi_topic=topic)
        dashboard.handle(None, None, FakeMessage(topic, payload))

        snapshot = dashboard.snapshot()
        self.assertEqual(snapshot["roi_timestamp"], 125.0)
        self.assertEqual(snapshot["rois"][0]["name"], "person")
        self.assertTrue(dashboard.roi_jpeg(0).startswith(b"\xff\xd8"))


class DashboardROITests(unittest.TestCase):
    def setUp(self):
        self.state = DashboardState(roi_topic="vision/roi", max_rois=4)
        self.image_b64 = base64.b64encode(JPEG_BYTES).decode("ascii")

    def test_dashboard_stores_images_separately_from_api_metadata(self):
        self.state.handle(
            None,
            None,
            FakeMessage(
                "vision/roi",
                {
                    "timestamp": 42.0,
                    "frame_size": {"width": 640, "height": 480},
                    "rois": [
                        {
                            "index": 0,
                            "name": "person",
                            "confidence": 0.91,
                            "bbox": [10, 20, 30, 40],
                            "crop_bbox": [7, 16, 36, 48],
                            "image_b64": self.image_b64,
                        }
                    ],
                },
            ),
        )

        snapshot = self.state.snapshot()
        self.assertEqual(snapshot["roi_timestamp"], 42.0)
        self.assertEqual(snapshot["roi_generation"], 1)
        self.assertTrue(snapshot["roi_instance_id"])
        self.assertEqual(snapshot["roi_frame_size"], {"width": 640, "height": 480})
        self.assertEqual(snapshot["rois"][0]["name"], "person")
        self.assertNotIn("image_b64", snapshot["rois"][0])
        self.assertEqual(self.state.roi_jpeg(0), JPEG_BYTES)
        self.assertIsNone(self.state.roi_jpeg(1))

        second_jpeg = b"\xff\xd8second-jpeg\xff\xd9"
        self.state.handle(
            None,
            None,
            FakeMessage(
                "vision/roi",
                {
                    "timestamp": 43.0,
                    "rois": [
                        {
                            "name": "car",
                            "image_b64": base64.b64encode(second_jpeg).decode("ascii"),
                        }
                    ],
                },
            ),
        )
        self.assertEqual(self.state.snapshot()["roi_generation"], 2)
        self.assertEqual(self.state.roi_jpeg(0, generation=1), JPEG_BYTES)
        self.assertEqual(self.state.roi_jpeg(0, generation=2), second_jpeg)
        self.assertIsNone(
            self.state.roi_jpeg(0, generation=2, instance_id="wrong-instance")
        )

    def test_corrupt_packet_is_ignored_and_empty_packet_clears_state(self):
        valid_payload = {
            "timestamp": 42.0,
            "rois": [{"name": "person", "image_b64": self.image_b64}],
        }
        self.state.handle(None, None, FakeMessage("vision/roi", valid_payload))

        corrupt_payload = {
            "timestamp": 43.0,
            "rois": [{"name": "person", "image_b64": "not-base64!"}],
        }
        self.state.handle(None, None, FakeMessage("vision/roi", corrupt_payload))
        self.assertEqual(self.state.snapshot()["roi_timestamp"], 42.0)

        self.state.handle(
            None,
            None,
            FakeMessage("vision/roi", {"timestamp": 41.0, "rois": []}),
        )
        self.assertEqual(self.state.snapshot()["roi_timestamp"], 42.0)
        self.assertIsNotNone(self.state.roi_jpeg(0))

        self.state.handle(
            None,
            None,
            FakeMessage("vision/roi", {"timestamp": 44.0, "rois": []}),
        )
        snapshot = self.state.snapshot()
        self.assertEqual(snapshot["roi_timestamp"], 44.0)
        self.assertEqual(snapshot["rois"], [])
        self.assertIsNone(self.state.roi_jpeg(0))


if __name__ == "__main__":
    unittest.main()
