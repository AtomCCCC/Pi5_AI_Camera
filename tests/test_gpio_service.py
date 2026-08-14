"""Message-bridge tests for ordered gimbal and manual-override commands."""

import sys
import types
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "pi5_assistant"
sys.path.insert(0, str(APP_ROOT))


try:
    import yaml  # noqa: F401
except ImportError:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda _stream: {}
    sys.modules["yaml"] = yaml_stub

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


from gpio_service.main import GPIOService  # noqa: E402


class FakeServo:
    def __init__(self):
        self.single = []
        self.paired = []

    def set_angle(self, servo, angle):
        applied = max(20, min(160, float(angle)))
        self.single.append((servo, applied))
        return applied

    def set_angles(self, angles):
        self.paired.append(angles)


class FakeMQTT:
    def __init__(self):
        self.messages = []

    def publish(self, topic, payload):
        self.messages.append((topic, payload))


class GPIOServiceCommandTests(unittest.TestCase):
    def setUp(self):
        self.service = GPIOService.__new__(GPIOService)
        self.service.servo = FakeServo()
        self.service.mqtt = FakeMQTT()
        self.service.control_topic = "control/command"
        self.service.manual_override_seconds = 3.0
        self.service._manual_override_until = 0.0

    def test_visual_gimbal_message_reaches_paired_servo_api(self):
        angles = {"1": 92, "2": 88}
        self.service._on_command({
            "type": "gimbal",
            "angles": angles,
            "source": "visual_pid",
        })
        self.assertEqual(self.service.servo.paired, [angles])

    def test_manual_servo_temporarily_blocks_pid_and_notifies_control(self):
        self.service._on_command({
            "type": "servo",
            "servo": 1,
            "angle": 180,
            "source": "llm",
            "session_id": "s1",
        })
        self.assertEqual(self.service.servo.single, [(1, 160)])
        self.assertEqual(len(self.service.mqtt.messages), 1)
        topic, payload = self.service.mqtt.messages[0]
        self.assertEqual(topic, "control/command")
        self.assertEqual(payload["type"], "manual_override")
        self.assertEqual(payload["angle"], 160)

        self.service._on_command({
            "type": "gimbal",
            "angles": {"1": 100, "2": 100},
            "source": "visual_pid",
        })
        self.assertEqual(self.service.servo.paired, [])


if __name__ == "__main__":
    unittest.main()
