"""LLM Orchestrator — main entry point.

Subscribes to command/in for user text.
Selects LLM backend (DeepSeek online / Hailo NPU / Ollama CPU).
Executes any tool calls the LLM requests.
Publishes final response to response/out.
"""

import sys
import os
import yaml
import json
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pi5_assistant.mqtt_client import MQTTClient
from llm_orchestrator.router import Router
from llm_orchestrator.deepseek_client import DeepSeekClient
from llm_orchestrator.hailo_client import HailoClient
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

    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

        self.mqtt = MQTTClient("llm", self.cfg["mqtt"]["broker"],
                               self.cfg["mqtt"]["port"])
        self.router = Router(self.cfg)

        self.tool_handlers = {
            "visual_detect": VisualDetectHandler(self.mqtt),
            "vlm_query": VLMQueryHandler(self.mqtt),
            "servo_write": ServoWriteHandler(self.mqtt),
            "gpio_write": GPIOWriteHandler(self.mqtt),
            "screen_display": ScreenDisplayHandler(self.mqtt),
        }

    def _get_llm(self):
        """Get the appropriate LLM client based on connectivity and hardware."""
        if self.router.should_use_online():
            logger.info("Using DeepSeek V4 (online)")
            return DeepSeekClient(self.cfg)
        elif self.router.should_use_hailo():
            logger.info("Using Qwen on Hailo-10H NPU")
            return HailoClient(self.cfg)
        logger.info("Using Qwen via Ollama (CPU fallback)")
        return OllamaClient(self.cfg)

    def run(self):
        self.mqtt.subscribe(self.cfg["mqtt"]["topic_command_in"], self._on_command)
        print("[LLM] Orchestrator started. Waiting for commands...")
        import threading
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            self.stop()

    def _on_command(self, payload):
        """Handle an incoming voice command."""
        text = payload.get("text", "")
        session_id = payload.get("session_id", "unknown")

        if not text.strip():
            return

        print(f"[LLM] [{session_id}] Processing: {text[:80]}...")

        messages = [
            {"role": "system", "content": self.cfg["system_prompt"]},
            {"role": "user", "content": text},
        ]

        llm = self._get_llm()

        try:
            response = llm.chat(messages, tools=TOOLS)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            self.mqtt.publish(self.cfg["mqtt"]["topic_response_out"], {
                "text": "Sorry, I'm having trouble connecting to my AI backend.",
                "session_id": session_id,
            })
            return

        while llm.is_tool_call(response):
            messages.append(response.choices[0].message if hasattr(response, 'choices') else response.get("message", {}))

            tool_calls = llm.extract_tool_calls(response)

            for tc in tool_calls:
                name = tc["name"]
                args = tc["arguments"]
                tool_id = tc["id"]

                handler = self.tool_handlers.get(name)
                if handler:
                    logger.info(f"  \u2192 Tool: {name}({args})")
                    result = handler.handle(args, session_id)
                else:
                    result = json.dumps({"error": f"Unknown tool: {name}"})

                messages.append(llm.build_tool_result_message(tool_id, result))

            try:
                response = llm.chat(messages, tools=TOOLS)
            except Exception as e:
                logger.error(f"LLM follow-up call failed: {e}")
                break

        final_text = llm.get_text(response)
        print(f"[LLM] [{session_id}] \u2192 {final_text[:80]}...")

        self.mqtt.publish(self.cfg["mqtt"]["topic_response_out"], {
            "text": final_text,
            "session_id": session_id,
        })

    def stop(self):
        if hasattr(self, '_llm'):
            self._llm.stop()
        self.mqtt.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    orch = LLMOrchestrator()
    orch.run()
