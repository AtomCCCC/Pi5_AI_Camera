"""Ollama client with NPU proxy support.

Routes requests to Hailo NPU proxy (port 8000) by default.
Proxy handles:
  - Plain chat → NPU (0% CPU, 20-35 tok/s)
  - Tool calls → CPU Ollama (localhost:11434)
If proxy is down, falls back to direct CPU Ollama.
"""

import json
import logging
import requests

logger = logging.getLogger(__name__)


class OllamaClient:
    """Client for local LLM via NPU proxy or CPU Ollama."""

    def __init__(self, config: dict):
        self.model = config["ollama"]["model"]
        self.base_url = config["ollama"]["base_url"]
        self.fallback_model = config["ollama"]["fallback_model"]
        self.fallback_url = config["ollama"].get("fallback_url", "http://localhost:11434")

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             tool_choice: str = "auto") -> dict:
        """Send chat request — routes via NPU proxy or direct CPU Ollama."""
        payload = {
            "model": self.model,
            "messages": messages,
            "options": {
                "temperature": 0.7,
                "num_predict": 1024,
            },
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        # Try NPU proxy first
        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning(f"NPU proxy error: {e}")

        # Fallback to direct CPU Ollama
        logger.info("Falling back to CPU Ollama")
        try:
            if tools:
                payload["model"] = self.fallback_model
            resp = requests.post(
                f"{self.fallback_url}/api/chat",
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"CPU Ollama error: {e}")
            raise

    def is_tool_call(self, response: dict) -> bool:
        msg = response.get("message", {})
        return "tool_calls" in msg and msg["tool_calls"]

    def extract_tool_calls(self, response: dict) -> list[dict]:
        msg = response.get("message", {})
        calls = []
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            calls.append({
                "id": fn.get("name", "unknown"),
                "name": fn.get("name", ""),
                "arguments": fn.get("arguments", {}),
            })
        return calls

    def get_text(self, response: dict) -> str:
        return response.get("message", {}).get("content", "")

    def build_tool_result_message(self, tool_call_id: str, result: str) -> dict:
        return {
            "role": "tool",
            "content": result,
        }
