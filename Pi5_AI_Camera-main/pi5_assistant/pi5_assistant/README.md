# Shared Package

Reusable utilities shared across all 5 services. Currently contains the MQTT wrapper that every service uses to publish and subscribe.

## Files

| File | Role |
|------|------|
| `__init__.py` | Package init |
| `mqtt_client.py` | MQTT wrapper — connect, publish, subscribe with auto-JSON-decode |

## MQTTClient Methodology

```
┌──────────────────────────────────────────────────────────────────────────┐
│                      MQTT CLIENT METHODOLOGY                              │
│                                                                           │
│  Every service creates its own instance:                                  │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │ mqtt = MQTTClient("service_name", "localhost", 1883)            │     │
│  │                                                                 │     │
│  │  mqtt.subscribe("topic/in", callback)  ← auto-parse JSON        │     │
│  │  mqtt.publish("topic/out", {"key": "val"})  ← auto-serialize   │     │
│  │  mqtt.stop()                              ← clean disconnect    │     │
│  └─────────────────────────────────────────────────────────────────┘     │
│                                                                           │
│  Internally:                                                              │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                                                                     │  │
│  │  paho.mqtt.client                                                   │  │
│  │      │                                                              │  │
│  │      ├── connect(broker, port)                                      │  │
│  │      ├── loop_start()  ← daemon thread                              │  │
│  │      ├── subscribe(topic)                                           │  │
│  │      ├── on_message → json.loads(payload) → user callback(dict)    │  │
│  │      └── publish(topic, json.dumps(payload))                        │  │
│  │                                                                     │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  Callback pattern:                                                        │
│    def my_callback(payload: dict):                                         │
│        # payload is already decoded from JSON                             │
│        text = payload.get("text")                                         │
│        session_id = payload.get("session_id")                             │
│                                                                           │
└──────────────────────────────────────────────────────────────────────────┘
```

### Usage

```python
from pi5_assistant.mqtt_client import MQTTClient

mqtt = MQTTClient(
    client_id="my_service",    # unique per service
    broker="localhost",        # MQTT broker address
    port=1883,                 # MQTT port
)

def on_message(payload: dict):
    print(payload.get("text"))

mqtt.subscribe("topic/name", on_message)
mqtt.publish("topic/name", {"key": "value"})  # auto JSON-serialized
```

The client runs the MQTT loop in a daemon thread. Callbacks receive parsed JSON dicts. No manual serialization/deserialization needed.
