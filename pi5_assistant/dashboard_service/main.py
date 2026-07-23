"""MQTT-backed browser dashboard for live camera and AI activity."""

import base64
import json
import os
import threading
import time

from flask import Flask, Response, jsonify, render_template_string
import paho.mqtt.client as mqtt


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pi 5 AI Camera</title><style>
body{background:#10151d;color:#e7edf5;font:16px system-ui;margin:0;padding:24px}main{max-width:1400px;margin:auto}
h1{margin:0 0 6px}.sub{color:#9baaba;margin-bottom:20px}.grid{display:grid;grid-template-columns:2fr 1fr;gap:18px}.card{background:#19212c;border:1px solid #2b3a4c;border-radius:12px;padding:16px}img{width:100%;background:#05080c;border-radius:8px;min-height:300px;object-fit:contain}.status{color:#77d6a3}.muted{color:#9baaba}.items{display:flex;flex-wrap:wrap;gap:8px}.tag{background:#26384c;border-radius:999px;padding:5px 10px}pre{white-space:pre-wrap;word-break:break-word;margin:8px 0 0;font:inherit;line-height:1.45}.time{font-size:.8rem;color:#9baaba}@media(max-width:800px){.grid{grid-template-columns:1fr}}
</style></head><body><main><h1>Pi 5 AI Camera</h1><div class="sub"><span class="status">●</span> Live MQTT dashboard · <span id="updated">Waiting for camera…</span></div>
<div class="grid"><section class="card"><img id="frame" alt="Live camera preview"><p class="time" id="frameTime">No preview received yet.</p></section>
<aside class="card"><h2>Detected objects</h2><div id="detections" class="items"><span class="muted">Waiting for detector…</span></div><h2>VLM scene analysis</h2><pre id="vlm" class="muted">No VLM response yet.</pre><h2>LLM response</h2><pre id="llm" class="muted">No LLM response yet.</pre></aside></div></main>
<script>let frameTs=0; async function refresh(){try{const s=await fetch('/api/state',{cache:'no-store'}).then(r=>r.json());document.getElementById('updated').textContent=s.updated_at?'Updated '+new Date(s.updated_at*1000).toLocaleTimeString():'Waiting for camera…';const d=document.getElementById('detections');d.innerHTML=s.detections.length?s.detections.map(x=>`<span class="tag">${x.name} ${Math.round(x.confidence*100)}%</span>`).join(''):'<span class="muted">No objects detected.</span>';document.getElementById('vlm').textContent=s.vlm||'No VLM response yet.';document.getElementById('llm').textContent=s.llm||'No LLM response yet.';if(s.frame_timestamp&&s.frame_timestamp!==frameTs){frameTs=s.frame_timestamp;document.getElementById('frame').src='/frame.jpg?t='+frameTs;document.getElementById('frameTime').textContent='Frame '+new Date(s.frame_timestamp*1000).toLocaleTimeString();}}catch(e){document.getElementById('updated').textContent='Dashboard reconnecting…'}} refresh();setInterval(refresh,700);</script></body></html>"""


class DashboardState:
    def __init__(self):
        self.lock = threading.Lock()
        self.frame = None
        self.frame_timestamp = 0.0
        self.detections = []
        self.vlm = ""
        self.llm = ""
        self.updated_at = 0.0

    def handle(self, _client, _userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        with self.lock:
            if message.topic == "vision/frame":
                encoded = payload.get("image_b64")
                if encoded:
                    self.frame = base64.b64decode(encoded)
                    self.frame_timestamp = float(payload.get("timestamp", time.time()))
                    self.detections = payload.get("detections", self.detections)
            elif message.topic in {"vision/detections", "vision/detect_result"}:
                self.detections = payload.get("detections", [])
            elif message.topic == "vision/result":
                self.vlm = payload.get("description") or payload.get("error", "")
            elif message.topic == "response/out":
                self.llm = payload.get("text", "")
            self.updated_at = time.time()

    def snapshot(self):
        with self.lock:
            return {"detections": self.detections, "vlm": self.vlm, "llm": self.llm,
                    "frame_timestamp": self.frame_timestamp, "updated_at": self.updated_at}

    def jpeg(self):
        with self.lock:
            return self.frame


def create_app():
    state = DashboardState()
    broker = os.environ.get("MQTT_BROKER", "127.0.0.1")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    client = mqtt.Client(client_id=f"dashboard_{os.getpid()}")
    client.on_message = state.handle
    client.connect(broker, port, keepalive=60)
    for topic in ("vision/frame", "vision/detections", "vision/detect_result", "vision/result", "response/out"):
        client.subscribe(topic)
    client.loop_start()

    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template_string(PAGE)

    @app.get("/api/state")
    def api_state():
        return jsonify(state.snapshot())

    @app.get("/frame.jpg")
    def frame():
        image = state.jpeg()
        if image is None:
            return Response(status=204)
        return Response(image, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.environ.get("DASHBOARD_PORT", "8080")), threaded=True)
