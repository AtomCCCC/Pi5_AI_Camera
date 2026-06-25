"""Shared MQTT client wrapper."""

import os
import json
import paho.mqtt.client as mqtt


class MQTTClient:
    """Thin wrapper around paho-mqtt with JSON payloads."""

    def __init__(self, service_name: str, broker: str = "localhost", port: int = 1883):
        self.client = mqtt.Client(client_id=f"{service_name}_{os.getpid()}")
        self.client.connect(broker, port, keepalive=60)
        self.client.loop_start()

    def publish(self, topic: str, payload: dict):
        self.client.publish(topic, json.dumps(payload))

    def subscribe(self, topic: str, callback):
        def _on_message(_client, _userdata, msg):
            payload = json.loads(msg.payload.decode())
            callback(payload)

        self.client.subscribe(topic)
        self.client.message_callback_add(topic, _on_message)

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()
