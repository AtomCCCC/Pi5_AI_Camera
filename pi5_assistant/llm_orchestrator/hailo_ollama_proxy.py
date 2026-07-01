"""Hailo NPU → Ollama-compatible HTTP proxy.

Listens on port 8000, accepts Ollama /api/chat format requests,
runs inference on Hailo-10H NPU, returns Ollama-formatted responses.

If tool calls are requested, falls back to real Ollama on CPU at port 11434.

Usage:
    python hailo_ollama_proxy.py
"""

import json
import logging
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests

logger = logging.getLogger(__name__)


class HailoLlamaServer:
    """Lazy-init singleton for Hailo LLM on NPU."""

    def __init__(self, hef_path: str, max_tokens: int = 400):
        self.hef_path = hef_path
        self.max_tokens = max_tokens
        self._vdevice = None
        self._llm = None
        self._lock = threading.Lock()

    def _init(self):
        from hailo_platform import VDevice
        from hailo_platform.genai import LLM
        from hailo_apps.python.core.common.defines import SHARED_VDEVICE_GROUP_ID

        params = VDevice.create_params()
        params.group_id = SHARED_VDEVICE_GROUP_ID
        self._vdevice = VDevice(params)
        self._llm = LLM(self._vdevice, self.hef_path)
        logger.info("NPU model loaded")

    def generate(self, messages: list[dict]) -> str:
        with self._lock:
            if self._llm is None:
                self._init()

            prompt = []
            for msg in messages:
                role = msg["role"]
                text = msg["content"]
                prompt.append({
                    "role": role,
                    "content": [{"type": "text", "text": text}],
                })

            text = self._llm.generate_all(
                prompt=prompt,
                temperature=0.1,
                seed=42,
                max_generated_tokens=self.max_tokens,
            )
            clean = text.split(". [{'type'")[0] if ". [{'type'" in text else text
            clean = clean.split("<|im_end|>")[0].strip()
            return clean

    def stop(self):
        if self._llm:
            self._llm.clear_context()
            self._llm.release()
        if self._vdevice:
            self._vdevice.release()


# ---------------- HTTP Handler ----------------


class OllamaProxyHandler(BaseHTTPRequestHandler):

    npu: HailoLlamaServer = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode() if length else "{}"
        data = json.loads(body)

        if self.path == "/api/chat":
            self._handle_chat(data)
        elif self.path == "/api/tags":
            self._handle_list_models()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_chat(self, data):
        messages = data.get("messages", [])
        has_tools = bool(data.get("tools"))

        if has_tools:
            # Tool calling → fallback to CPU Ollama
            resp = requests.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": data.get("model", "qwen2.5:3b"),
                    "messages": messages,
                    "tools": data["tools"],
                    "stream": False,
                },
                timeout=120,
            )
            self.send_response(resp.status_code)
            self.end_headers()
            self.wfile.write(resp.content)
        else:
            # NPU inference
            text = self.npu.generate(messages)
            response = {
                "model": "hailo-npu",
                "created_at": "",
                "message": {"role": "assistant", "content": text},
                "done": True,
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

    def _handle_list_models(self):
        models = {
            "models": [
                {"name": "qwen2.5-instruct:1.5b-npu", "modified_at": ""},
                {"name": "qwen2.5:1.5b-npu", "modified_at": ""},
            ]
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(models).encode())

    def log_message(self, format, *args):
        logger.info(f"NGROK {args}")


# ---------------- Main ----------------


HEF_PATH = "/usr/local/hailo/resources/models/hailo10h/Qwen2.5-1.5B-Instruct.hef"


def run_server(port=8000):
    logging.basicConfig(level=logging.INFO)

    logger.info("Starting Hailo NPU Ollama proxy on port %d...", port)
    OllamaProxyHandler.npu = HailoLlamaServer(HEF_PATH)
    logger.info("Warming up NPU model...")

    server = HTTPServer(("127.0.0.1", port), OllamaProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        OllamaProxyHandler.npu.stop()
        server.shutdown()


if __name__ == "__main__":
    run_server()
