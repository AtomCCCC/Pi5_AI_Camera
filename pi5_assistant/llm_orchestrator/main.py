"""LLM Orchestrator — main entry point.

Subscribes to command/in for user text.
Selects LLM backend (DeepSeek online / Ollama offline via NPU proxy).
Executes any tool calls the LLM requests.
Publishes final response to response/out.
"""

import sys
import os
import yaml
import json
import logging
import queue
import threading
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pi5_assistant.mqtt_client import MQTTClient
from llm_orchestrator.router import Router
from llm_orchestrator.deepseek_client import DeepSeekClient
from llm_orchestrator.ollama_client import OllamaClient
from llm_orchestrator.tool_definitions import TOOLS
from llm_orchestrator.tool_handlers.visual_detect import VisualDetectHandler
from llm_orchestrator.tool_handlers.vlm_query import VLMQueryHandler
from llm_orchestrator.tool_handlers.servo_write import ServoWriteHandler
from llm_orchestrator.tool_handlers.gpio_write import GPIOWriteHandler
from llm_orchestrator.tool_handlers.screen_display import ScreenDisplayHandler

logger = logging.getLogger(__name__)


class LLMOrchestrator:
    """Central AI reasoning — routes, calls, dispatches tools."""

    def __init__(self, config_path: str | None = None):
        config_path = config_path or Path(__file__).with_name("config.yaml")
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

        self.mqtt = MQTTClient("llm", self.cfg["mqtt"]["broker"],
                               self.cfg["mqtt"]["port"])
        self.router = Router(self.cfg)

        mqtt_cfg = self.cfg["mqtt"]
        hardware_cfg = self.cfg.get("hardware", {})
        self.max_tool_rounds = int(self.cfg.get("orchestrator", {}).get(
            "max_tool_rounds", 4
        ))
        if self.max_tool_rounds < 1:
            raise ValueError("orchestrator.max_tool_rounds must be at least 1")

        self.tool_handlers = {
            "visual_detect": VisualDetectHandler(
                self.mqtt,
                mqtt_cfg["topic_vision_detect"],
                mqtt_cfg["topic_vision_detect_result"],
            ),
            "vlm_query": VLMQueryHandler(
                self.mqtt,
                mqtt_cfg["topic_vision_query"],
                mqtt_cfg["topic_vision_result"],
            ),
            "servo_write": ServoWriteHandler(
                self.mqtt, mqtt_cfg["topic_gpio_command"]
            ),
            "gpio_write": GPIOWriteHandler(
                self.mqtt,
                hardware_cfg.get("allowed_gpio_pins", []),
                mqtt_cfg["topic_gpio_command"],
            ),
            "screen_display": ScreenDisplayHandler(
                self.mqtt, mqtt_cfg["topic_gpio_command"]
            ),
        }
        self._command_queue = queue.Queue()
        self._stop_event = threading.Event()
        self._worker = None

    def _get_llm(self):
        """Get the appropriate LLM client based on connectivity.

        Online  → DeepSeek V4 API (full tool calling)
        Offline → OllamaClient → NPU proxy :8000 → NPU for chat, CPU fallback for tools
        """
        if self.router.should_use_online():
            logger.info("Using DeepSeek V4 (online)")
            return DeepSeekClient(self.cfg)
        logger.info("Using Ollama via NPU proxy")
        return OllamaClient(self.cfg)

    def run(self):
        # Paho invokes subscription callbacks from its network thread.  The LLM
        # and Vision request/response handlers can block for seconds, so run
        # them on one worker thread and keep the network loop free to receive
        # their MQTT replies.
        self._worker = threading.Thread(
            target=self._command_worker, name="llm-command-worker", daemon=True
        )
        self._worker.start()
        self.mqtt.subscribe(self.cfg["mqtt"]["topic_command_in"], self._on_command)
        print("[LLM] Orchestrator started. Waiting for commands...")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            self.stop()

    def _on_command(self, payload):
        """Queue an incoming command without blocking Paho's network thread."""
        if not isinstance(payload, dict):
            logger.warning("Ignoring non-object command payload")
            return
        self._command_queue.put(payload)

    def _command_worker(self):
        while not self._stop_event.is_set():
            payload = self._command_queue.get()
            if payload is None:
                return
            try:
                self._process_command(payload)
            except Exception:
                logger.exception("Unexpected failure while processing LLM command")

    @staticmethod
    def _history_from_payload(payload):
        history = payload.get("history", [])
        if not isinstance(history, list):
            return []
        messages = []
        for item in history[-20:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role in {"user", "assistant"} and isinstance(content, str):
                messages.append({"role": role, "content": content})
        return messages

    def _process_command(self, payload):
        """Handle one queued command on the dedicated worker thread."""
        text = payload.get("text", "")
        session_id = payload.get("session_id", "unknown")

        if not isinstance(text, str) or not text.strip():
            return

        print(f"[LLM] [{session_id}] Processing: {text[:80]}...")

        messages = [
            {"role": "system", "content": self.cfg["system_prompt"]},
        ]
        history = self._history_from_payload(payload)
        messages.extend(history)
        if not history or history[-1] != {"role": "user", "content": text}:
            messages.append({"role": "user", "content": text})

        try:
            llm = self._get_llm()
            response = llm.chat(messages, tools=TOOLS)
            for _ in range(self.max_tool_rounds):
                if not llm.is_tool_call(response):
                    break
                messages.append(
                    response.choices[0].message
                    if hasattr(response, "choices")
                    else response.get("message", {})
                )

                for tc in llm.extract_tool_calls(response):
                    name = tc["name"]
                    args = tc["arguments"]
                    tool_id = tc["id"]
                    handler = self.tool_handlers.get(name)
                    try:
                        if handler:
                            logger.info("  \u2192 Tool: %s(%s)", name, args)
                            result = handler.handle(args, session_id)
                        else:
                            result = json.dumps({"error": f"Unknown tool: {name}"})
                    except Exception:
                        logger.exception("Tool %s failed", name)
                        result = json.dumps({"error": f"Tool {name} failed"})
                    messages.append(llm.build_tool_result_message(tool_id, result))

                response = llm.chat(messages, tools=TOOLS)
            else:
                if llm.is_tool_call(response):
                    raise RuntimeError("LLM exceeded the tool-call limit")

            final_text = llm.get_text(response)
        except Exception as exc:
            logger.exception("LLM command failed: %s", exc)
            final_text = "Sorry, I'm having trouble completing that request."

        print(f"[LLM] [{session_id}] \u2192 {final_text[:80]}...")

        self.mqtt.publish(self.cfg["mqtt"]["topic_response_out"], {
            "text": final_text,
            "session_id": session_id,
        })

    def stop(self):
        self._stop_event.set()
        self._command_queue.put(None)
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2)
        self.mqtt.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    orch = LLMOrchestrator()
    orch.run()
