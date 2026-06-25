"""Vision-Language Model engine.

Supports two interchangeable backends:
  - Path A: Hailo-10H built-in VLM (fast, zero CPU load)
  - Path B: Qwen2.5-VL-3B via Ollama on CPU (Qwen-family, slower)

Configure via config.yaml: vlm.mode = "hailo" | "qwen-cpu"
"""

import subprocess
import json
import os
import tempfile
import cv2
import numpy as np


class VLMEngine:
    """Abstraction over VLM backends."""

    def __init__(self, mode: str, config: dict):
        self.mode = mode
        self.config = config

        if mode == "hailo":
            self._impl = HailoVLM(config)
        elif mode == "qwen-cpu":
            self._impl = QwenVLM(config)
        else:
            raise ValueError(f"Unknown VLM mode: {mode}")

    def query(self, frame: np.ndarray, prompt: str) -> str:
        """Ask a question about the current camera frame."""
        return self._impl.query(frame, prompt)


# ── Path A: Hailo-10H Native VLM ──────────────────────────────


class HailoVLM:
    """Runs Hailo's pre-built VLM entirely on the Hailo-10H NPU."""

    def __init__(self, config: dict):
        self.app = config["vlm"]["hailo_app"]
        self.input = config["vlm"]["hailo_input"]

    def query(self, frame: np.ndarray, prompt: str) -> str:
        # Save frame temporarily
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            img_path = tmp.name
            cv2.imwrite(img_path, frame)

        try:
            result = subprocess.run(
                [
                    "python", "-m", self.app,
                    "--input", img_path,
                    "--prompt", prompt,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.stdout.strip()
        finally:
            os.unlink(img_path)


# ── Path B: Qwen2.5-VL-3B on CPU via Ollama ──────────────────


class QwenVLM:
    """Runs Qwen2.5-VL on the Pi 5 CPU via Ollama."""

    def __init__(self, config: dict):
        self.model = config["vlm"]["qwen_model"]
        self.timeout = config["vlm"]["qwen_timeout"]

    def query(self, frame: np.ndarray, prompt: str) -> str:
        import ollama

        # Save frame temporarily
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            img_path = tmp.name
            cv2.imwrite(img_path, frame)

        try:
            response = ollama.chat(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": prompt,
                    "images": [img_path],
                }],
                options={"num_predict": 256, "temperature": 0.1},
            )
            return response["message"]["content"]
        finally:
            os.unlink(img_path)
