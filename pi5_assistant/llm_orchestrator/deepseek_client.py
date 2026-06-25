"""DeepSeek V4 API client.

OpenAI-compatible API at https://api.deepseek.com
Models: deepseek-v4-pro, deepseek-v4-flash
"""

import os
import json
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)


class DeepSeekClient:
    """Client for the DeepSeek V4 API."""

    def __init__(self, config: dict):
        api_key = os.environ.get(config["deepseek"]["api_key_env"])
        if not api_key:
            raise ValueError(f"Missing environment variable: {config['deepseek']['api_key_env']}")

        self.client = OpenAI(
            api_key=api_key,
            base_url=config["deepseek"]["base_url"],
        )
        self.model = config["deepseek"]["model"]
        self.timeout = config["deepseek"]["timeout"]

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             tool_choice: str = "auto") -> dict:
        """Send a chat completion request.
        
        Args:
            messages: Conversation history (system + user + assistant + tool)
            tools: Tool definitions the model may call
            tool_choice: "auto", "none", or "required"
        
        Returns:
            Full response object with .choices[0].message
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        response = self.client.chat.completions.create(**kwargs, timeout=self.timeout)
        return response

    def is_tool_call(self, response) -> bool:
        """Check if the response contains a tool call."""
        msg = response.choices[0].message
        return hasattr(msg, "tool_calls") and msg.tool_calls is not None

    def extract_tool_calls(self, response) -> list[dict]:
        """Extract tool calls from response as structured dicts."""
        msg = response.choices[0].message
        calls = []
        for tc in msg.tool_calls:
            calls.append({
                "id": tc.id,
                "name": tc.function.name,
                "arguments": json.loads(tc.function.arguments),
            })
        return calls

    def get_text(self, response) -> str:
        """Extract text content from response."""
        return response.choices[0].message.content or ""

    def build_tool_result_message(self, tool_call_id: str, result: str) -> dict:
        """Build a tool result message to append to conversation."""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result,
        }
