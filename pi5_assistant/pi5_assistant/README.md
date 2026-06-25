# Shared Package

Reusable utilities shared across all 5 services.

## Files

| File | Role |
|------|------|
| `__init__.py` | Package init |
| `mqtt_client.py` | MQTT wrapper — connect, publish, subscribe with auto-decode |

## MQTTClient

All services instantiate via:
```python
from pi5_assistant.mqtt_client import MQTTClient

mqtt = MQTTClient(
    client_id="my_service",    # unique per service
    broker="localhost",        # MQTT broker address
    port=1883,                 # MQTT port
)
mqtt.subscribe("topic/name", callback)
mqtt.publish("topic/name", {"key": "value"})  # auto JSON-serialized
```

The client runs the MQTT loop in a daemon thread. Callbacks receive parsed JSON dicts.
