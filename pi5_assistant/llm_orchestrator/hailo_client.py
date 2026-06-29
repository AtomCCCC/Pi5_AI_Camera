"""Hailo-10H NPU native LLM client.

Uses hailo_platform.genai.LLM to run Qwen 2.5 1.5B directly on the Hailo-10H NPU.
20-35 tok/s, 0% CPU load.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


class HailoClient:
    """Client for local LLM inference on Hailo-10H NPU."""

    def __init__(self, config: dict):
        self.hef_path = config["hailo"]["hef_path"]
        self.max_tokens = config["hailo"].get("max_tokens", 200)
        self.temperature = config["hailo"].get("temperature", 0.1)
        self._vdevice = None
        self._llm = None

    def _ensure_initialized(self):
        if self._llm is not None:
            return
        try:
            from hailo_platform import VDevice
            from hailo_platform.genai import LLM
            from hailo_apps.python.core.common.defines import SHARED_VDEVICE_GROUP_ID
        except ImportError:
            raise RuntimeError(
                "Hailo platform not available. "
                "Install hailo-apps and ensure venv has --system-site-packages."
            )

        logger.info("Initializing Hailo device...")
        params = VDevice.create_params()
        params.group_id = SHARED_VDEVICE_GROUP_ID
        self._vdevice = VDevice(params)
        logger.info("Loading LLM model on NPU...")
        self._llm = LLM(self._vdevice, self.hef_path)
        logger.info("Hailo NPU ready")

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             tool_choice: str = "auto") -> dict:
        """Send a chat completion request to Hailo NPU.

        Note: Hailo native LLM does not support tool calling.
        Tools parameter is accepted for interface compatibility but ignored.
        """
        self._ensure_initialized()

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
            temperature=self.temperature,
            seed=42,
            max_generated_tokens=self.max_tokens,
        )

        clean_text = text.split(". [{'type'")[0] if ". [{'type'" in text else text
        clean_text = clean_text.split("<|im_end|>")[0].strip()
        return {"message": {"content": clean_text}}

    def is_tool_call(self, response: dict) -> bool:
        return False

    def extract_tool_calls(self, response: dict) -> list[dict]:
        return []

    def get_text(self, response: dict) -> str:
        return response.get("message", {}).get("content", "")

    def build_tool_result_message(self, tool_call_id: str, result: str) -> dict:
        return {
            "role": "tool",
            "content": result,
        }

    def stop(self):
        if self._llm:
            try:
                self._llm.clear_context()
                self._llm.release()
            except Exception as e:
                logger.warning(f"Error releasing LLM: {e}")
            self._llm = None
        if self._vdevice:
            try:
                self._vdevice.release()
            except Exception as e:
                logger.warning(f"Error releasing VDevice: {e}")
            self._vdevice = None
