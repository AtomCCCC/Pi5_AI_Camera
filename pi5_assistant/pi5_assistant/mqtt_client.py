"""Shared MQTT client wrapper."""

import os
import json
import logging
import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MQTTClient:
    """Thin wrapper around paho-mqtt with JSON payloads."""

    def __init__(self, service_name: str, broker: str = "localhost", port: int = 1883):
        self.client = mqtt.Client(client_id=f"{service_name}_{os.getpid()}")
        self.client.connect(broker, port, keepalive=60)
        self.client.loop_start()

    def publish(self, topic: str, payload: dict):
        """Publish a JSON object and return the paho publish result."""
        return self.client.publish(topic, json.dumps(payload))

    def subscribe(self, topic: str, callback):
        def _on_message(_client, _userdata, msg):
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                logger.warning("Ignoring invalid JSON on MQTT topic %s", msg.topic)
                return

            try:
                callback(payload)
            except Exception:
                # An application callback must not kill Paho's network thread.
                logger.exception("MQTT callback failed for topic %s", msg.topic)

        self.client.subscribe(topic)
        self.client.message_callback_add(topic, _on_message)

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()
