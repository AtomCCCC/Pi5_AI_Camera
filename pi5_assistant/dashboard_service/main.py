"""MQTT-backed browser dashboard for live camera and AI activity."""

import base64
import binascii
import copy
import json
import math
import os
import threading
import time
import uuid

from flask import Flask, Response, jsonify, render_template_string
import paho.mqtt.client as mqtt


PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pi 5 AI Camera</title>
  <style>
    :root{color-scheme:dark;--bg:#10151d;--card:#19212c;--line:#2b3a4c;--text:#e7edf5;--muted:#9baaba;--green:#77d6a3}
    *{box-sizing:border-box}
    body{background:var(--bg);color:var(--text);font:16px system-ui,sans-serif;margin:0;padding:24px}
    main{max-width:1500px;margin:auto}
    h1{margin:0 0 6px}h2{font-size:1.1rem;margin:0 0 12px}
    .sub{color:var(--muted);margin-bottom:20px}.status{color:var(--green)}
    .layout{display:grid;grid-template-columns:minmax(0,2fr) minmax(300px,1fr);gap:18px}
    .visuals{display:grid;gap:18px;min-width:0}
    .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
    .card-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px}
    #frame{display:block;width:100%;min-height:360px;max-height:68vh;background:#05080c;border-radius:8px;object-fit:contain}
    .roi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
    .roi-card{margin:0;background:#111821;border:1px solid var(--line);border-radius:9px;padding:9px}
    .roi-card img{display:block;width:100%;height:190px;background:#05080c;border-radius:6px;object-fit:contain}
    .roi-card figcaption{margin-top:8px;font-size:.86rem;line-height:1.35;color:var(--muted)}
    .empty{display:grid;place-items:center;min-height:150px;border:1px dashed #34465b;border-radius:8px;color:var(--muted)}
    .muted,.time{color:var(--muted)}.time{font-size:.8rem;margin:9px 0 0}
    .items{display:flex;flex-wrap:wrap;gap:8px}.tag{background:#26384c;border-radius:999px;padding:5px 10px}
    pre{white-space:pre-wrap;word-break:break-word;margin:8px 0 22px;font:inherit;line-height:1.45}
    @media(max-width:900px){.layout{grid-template-columns:1fr}#frame{min-height:260px}}
  </style>
</head>
<body>
<main>
  <h1>Pi 5 AI Camera</h1>
  <div class="sub"><span class="status">●</span> MQTT 实时看板 · <span id="updated">等待摄像头数据…</span></div>
  <div class="layout">
    <div class="visuals">
      <section class="card">
        <div class="card-head"><h2>摄像头画面</h2><span class="time" id="frameTime">尚未收到画面</span></div>
        <img id="frame" alt="实时摄像头画面">
      </section>
      <section class="card">
        <div class="card-head"><h2>实时 ROI</h2><span class="time" id="roiSummary">等待 ROI 数据</span></div>
        <div id="roiGrid" class="roi-grid"><div class="empty">检测到目标后显示 ROI</div></div>
      </section>
    </div>
    <aside class="card">
      <h2>检测目标</h2>
      <div id="detections" class="items"><span class="muted">等待检测器数据…</span></div>
      <h2 style="margin-top:24px">VLM 场景分析</h2>
      <pre id="vlm" class="muted">尚无 VLM 响应</pre>
      <h2>LLM 响应</h2>
      <pre id="llm" class="muted">尚无 LLM 响应</pre>
    </aside>
  </div>
</main>
<script>
  let frameTs = 0;
  let roiTs = 0;
  let roiGeneration = 0;
  let roiInstanceId = '';
  let refreshInFlight = false;

  function renderDetections(detections) {
    const root = document.getElementById('detections');
    root.replaceChildren();
    if (!detections.length) {
      const empty = document.createElement('span');
      empty.className = 'muted';
      empty.textContent = '当前未检测到目标';
      root.appendChild(empty);
      return;
    }
    detections.forEach((detection) => {
      const tag = document.createElement('span');
      tag.className = 'tag';
      const confidence = Math.round(Number(detection.confidence || 0) * 100);
      tag.textContent = `${detection.name || 'unknown'} ${confidence}%`;
      root.appendChild(tag);
    });
  }

  function renderRois(rois, timestamp, generation, instanceId) {
    const root = document.getElementById('roiGrid');
    root.replaceChildren();
    document.getElementById('roiSummary').textContent =
      rois.length ? `${rois.length} 个目标 · ${new Date(timestamp * 1000).toLocaleTimeString()}` : '当前无目标';
    if (!rois.length) {
      const empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = '当前帧没有可显示的 ROI';
      root.appendChild(empty);
      return;
    }
    rois.forEach((roi, imageIndex) => {
      const figure = document.createElement('figure');
      figure.className = 'roi-card';
      const image = document.createElement('img');
      image.alt = `${roi.name || '目标'} ROI`;
      image.src = `/roi/${encodeURIComponent(instanceId)}/${generation}/${imageIndex}.jpg`;
      const caption = document.createElement('figcaption');
      const confidence = Math.round(Number(roi.confidence || 0) * 100);
      const crop = Array.isArray(roi.crop_bbox) ? roi.crop_bbox : [];
      const size = crop.length === 4 ? ` · 裁剪 ${crop[2]}×${crop[3]}` : '';
      caption.textContent = `${roi.name || 'unknown'} ${confidence}%${size}`;
      figure.append(image, caption);
      root.appendChild(figure);
    });
  }

  async function refresh() {
    if (refreshInFlight) return;
    refreshInFlight = true;
    try {
      const response = await fetch('/api/state', {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const state = await response.json();
      document.getElementById('updated').textContent = state.updated_at
        ? `更新于 ${new Date(state.updated_at * 1000).toLocaleTimeString()}`
        : '等待摄像头数据…';
      renderDetections(Array.isArray(state.detections) ? state.detections : []);
      document.getElementById('vlm').textContent = state.vlm || '尚无 VLM 响应';
      document.getElementById('llm').textContent = state.llm || '尚无 LLM 响应';

      if (state.roi_instance_id && state.roi_instance_id !== roiInstanceId) {
        roiInstanceId = state.roi_instance_id;
        roiGeneration = 0;
        roiTs = 0;
      }
      if (state.frame_timestamp && state.frame_timestamp > frameTs) {
        frameTs = state.frame_timestamp;
        document.getElementById('frame').src = `/frame.jpg?t=${encodeURIComponent(frameTs)}`;
        document.getElementById('frameTime').textContent =
          new Date(frameTs * 1000).toLocaleTimeString();
      }
      if (state.roi_generation && state.roi_generation > roiGeneration) {
        roiGeneration = state.roi_generation;
        roiTs = state.roi_timestamp;
        renderRois(
          Array.isArray(state.rois) ? state.rois : [],
          roiTs,
          roiGeneration,
          roiInstanceId
        );
      }
    } catch (_error) {
      document.getElementById('updated').textContent = '看板正在重新连接…';
    } finally {
      refreshInFlight = false;
    }
  }

  refresh();
  setInterval(refresh, 250);
</script>
</body>
</html>"""


def _timestamp(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return time.time()
    return parsed if math.isfinite(parsed) and parsed > 0 else time.time()


def _decode_jpeg(encoded):
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        image = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not image.startswith(b"\xff\xd8"):
        return None
    return image


class DashboardState:
    def __init__(self, roi_topic="vision/roi", max_rois=16):
        self.lock = threading.Lock()
        self.roi_topic = roi_topic
        self.max_rois = max(1, int(max_rois))
        self.roi_instance_id = uuid.uuid4().hex
        self.frame = None
        self.frame_timestamp = 0.0
        self.roi_frames = []
        self.roi_timestamp = 0.0
        self.roi_generation = 0
        self.roi_history = {}
        self.roi_history_size = 16
        self.rois = []
        self.roi_frame_size = {}
        self.detections = []
        self.vlm = ""
        self.llm = ""
        self.updated_at = 0.0

    def handle(self, _client, _userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return

        if message.topic == "vision/frame":
            image = _decode_jpeg(payload.get("image_b64"))
            if image is None:
                return
            message_timestamp = _timestamp(payload.get("timestamp"))
            with self.lock:
                if message_timestamp < self.frame_timestamp:
                    return
                self.frame = image
                self.frame_timestamp = message_timestamp
                detections = payload.get("detections")
                if isinstance(detections, list):
                    self.detections = detections
                self.updated_at = time.time()
            return

        if message.topic == self.roi_topic:
            raw_rois = payload.get("rois")
            if not isinstance(raw_rois, list):
                return
            frames = []
            rois = []
            for raw_roi in raw_rois[:self.max_rois]:
                if not isinstance(raw_roi, dict):
                    continue
                image = _decode_jpeg(raw_roi.get("image_b64"))
                if image is None:
                    continue
                metadata = {
                    key: copy.deepcopy(value)
                    for key, value in raw_roi.items()
                    if key != "image_b64"
                }
                metadata["image_index"] = len(frames)
                frames.append(image)
                rois.append(metadata)
            if raw_rois and not frames:
                return
            message_timestamp = _timestamp(payload.get("timestamp"))
            with self.lock:
                if message_timestamp < self.roi_timestamp:
                    return
                self.roi_frames = frames
                self.rois = rois
                self.roi_timestamp = message_timestamp
                self.roi_generation += 1
                self.roi_history[self.roi_generation] = frames
                while len(self.roi_history) > self.roi_history_size:
                    oldest_generation = next(iter(self.roi_history))
                    del self.roi_history[oldest_generation]
                frame_size = payload.get("frame_size")
                self.roi_frame_size = (
                    copy.deepcopy(frame_size) if isinstance(frame_size, dict) else {}
                )
                self.updated_at = time.time()
            return

        with self.lock:
            if message.topic in {"vision/detections", "vision/detect_result"}:
                self.detections = payload.get("detections", [])
            elif message.topic == "vision/result":
                self.vlm = payload.get("description") or payload.get("error", "")
            elif message.topic == "response/out":
                self.llm = payload.get("text", "")
            self.updated_at = time.time()

    def snapshot(self):
        with self.lock:
            return {
                "detections": copy.deepcopy(self.detections),
                "vlm": self.vlm,
                "llm": self.llm,
                "frame_timestamp": self.frame_timestamp,
                "roi_timestamp": self.roi_timestamp,
                "roi_generation": self.roi_generation,
                "roi_instance_id": self.roi_instance_id,
                "roi_frame_size": copy.deepcopy(self.roi_frame_size),
                "rois": copy.deepcopy(self.rois),
                "updated_at": self.updated_at,
            }

    def jpeg(self):
        with self.lock:
            return self.frame

    def roi_jpeg(self, index, generation=None, instance_id=None):
        with self.lock:
            if instance_id is not None and instance_id != self.roi_instance_id:
                return None
            if generation is None:
                generation = self.roi_generation
            frames = self.roi_history.get(generation, [])
            if 0 <= index < len(frames):
                return frames[index]
            return None


def create_app():
    broker = os.environ.get("MQTT_BROKER", "127.0.0.1")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    roi_topic = os.environ.get("MQTT_TOPIC_ROI", "vision/roi")
    max_rois = int(os.environ.get("DASHBOARD_MAX_ROIS", "16"))
    state = DashboardState(roi_topic=roi_topic, max_rois=max_rois)
    client = mqtt.Client(client_id=f"dashboard_{os.getpid()}")
    client.on_message = state.handle
    topics = (
        "vision/frame",
        "vision/detections",
        "vision/detect_result",
        "vision/result",
        "response/out",
        roi_topic,
    )

    def subscribe_on_connect(connected_client, _userdata, _flags, _reason_code,
                             _properties=None):
        for topic in dict.fromkeys(topics):
            connected_client.subscribe(topic)

    client.on_connect = subscribe_on_connect
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect(broker, port, keepalive=60)
    client.loop_start()

    app = Flask(__name__)
    app.config["DASHBOARD_STATE"] = state
    app.extensions["dashboard_mqtt_client"] = client

    @app.get("/")
    def index():
        return render_template_string(PAGE)

    @app.get("/api/state")
    def api_state():
        response = jsonify(state.snapshot())
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/frame.jpg")
    def frame():
        image = state.jpeg()
        if image is None:
            return Response(status=204)
        return Response(image, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/roi/<string:instance_id>/<int:generation>/<int:index>.jpg")
    def roi_frame(instance_id, generation, index):
        image = state.roi_jpeg(index, generation, instance_id)
        if image is None:
            return Response(status=404)
        return Response(image, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.environ.get("DASHBOARD_PORT", "8080")), threaded=True)
