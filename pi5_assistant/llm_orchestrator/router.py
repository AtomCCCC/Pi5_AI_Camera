"""Connectivity router — detects internet and selects the appropriate LLM backend.

Online  → DeepSeek V4 API
Offline → Hailo-10H NPU (Qwen 2.5 1.5B, 0% CPU)
No NPU  → Ollama CPU (Qwen 2.5 3B)
"""

import socket
import time
import logging

logger = logging.getLogger(__name__)


class Router:
    """Auto-detects connectivity and provides the right LLM client."""

    def __init__(self, config: dict):
        self.config = config
        self._last_check: float = 0
        self._cached_online: bool | None = None
        self._check_interval = config["router"]["connectivity_check_interval"]

    def get_online_status(self) -> bool:
        """Check if internet is available (cached for check_interval seconds)."""
        now = time.time()
        if self._cached_online is None or (now - self._last_check) > self._check_interval:
            self._cached_online = self._ping()
            self._last_check = now
            status = "online" if self._cached_online else "offline"
            logger.info(f"Connectivity: {status}")
        return self._cached_online

    def should_use_online(self) -> bool:
        """Returns True if online LLM should be used."""
        if not self.config["router"]["prefer_online"]:
            return False
        return self.get_online_status()

    def should_use_hailo(self) -> bool:
        """Returns True if Hailo NPU is configured and available."""
        return bool(self.config.get("hailo", {}).get("hef_path"))

    @staticmethod
    def _ping() -> bool:
        """Quick connectivity check by attempting to reach DeepSeek."""
        try:
            socket.create_connection(
                ("api.deepseek.com", 443),
                timeout=3
            )
            return True
        except OSError:
            return False
