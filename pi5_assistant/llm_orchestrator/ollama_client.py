"""Ollama client for local Qwen inference on Hailo-10H.

Qwen 2.5 1.5B runs at 20-35 tok/s on the Hailo-10H NPU.
Falls back to Qwen 2.5 3B on CPU if Hailo is unavailable.
"""

import json
import logging
import requests

logger = logging.getLogger(__name__)


class OllamaClient:
    """Client for local LLM via Ollama (Qwen on Hailo-10H)."""

    def __init__(self, config: dict):
        self.model = config["ollama"]["model"]
        self.base_url = config["ollama"]["base_url"]
        self.fallback_model = config["ollama"]["fallback_model"]

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             tool_choice: str = "auto") -> dict:
        """Send a chat completion request to local Ollama."""
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

        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning(f"Ollama error with {self.model}: {e}")
            # Fallback to CPU model
            if self.model != self.fallback_model:
                logger.info(f"Falling back to {self.fallback_model}")
                payload["model"] = self.fallback_model
                resp = requests.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=120,
                )
                resp.raise_for_status()
                return resp.json()
            raise

    def is_tool_call(self, response: dict) -> bool:
        """Check if the response contains a tool call."""
        msg = response.get("message", {})
        return "tool_calls" in msg and msg["tool_calls"]

    def extract_tool_calls(self, response: dict) -> list[dict]:
        """Extract tool calls from Ollama response."""
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
        """Extract text content from Ollama response."""
        return response.get("message", {}).get("content", "")

    def build_tool_result_message(self, tool_call_id: str, result: str) -> dict:
        """Build a tool result message for Ollama conversation."""
        return {
            "role": "tool",
            "content": result,
        }
