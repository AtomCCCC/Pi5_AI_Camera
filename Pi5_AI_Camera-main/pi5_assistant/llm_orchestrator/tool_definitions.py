"""Tool definitions shared across both DeepSeek and Ollama backends.

Each entry follows OpenAI-compatible JSON Schema format.
These are passed to the LLM as the `tools` parameter.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "visual_detect",
            "description": "Get current object detections from the camera in real time. Returns class names, confidence scores, and bounding boxes for every detected object in the latest frame.",
            "parameters": {
                "type": "object",
                "properties": {
                    "classes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional filter: only return objects matching these class names (e.g. ['person', 'car'])"
                    },
                    "min_confidence": {
                        "type": "number",
                        "description": "Minimum confidence threshold (0.0 to 1.0). Defaults to 0.5."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vlm_query",
            "description": "Ask a detailed question about the current camera view. Use this for scene understanding, reading text, identifying colors, counting objects, or any visual question that needs language reasoning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Your question about the scene. Be specific. Examples: 'What color is the car in the driveway?' or 'How many people are in the room?' or 'Read the text on that whiteboard.'"
                    }
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "servo_write",
            "description": "Set a servo motor to a specific angle. Use this to move physical appendages, point the camera, or trigger mechanical actions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "servo": {
                        "type": "integer",
                        "enum": [1, 2],
                        "description": "Servo number: 1 or 2"
                    },
                    "angle": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 180,
                        "description": "Target angle in degrees (0 = min, 180 = max)"
                    }
                },
                "required": ["servo", "angle"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gpio_write",
            "description": "Set a GPIO pin HIGH (3.3V) or LOW (0V). Use this to control LEDs, relays, buzzers, or any digital device connected to GPIO.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pin": {
                        "type": "integer",
                        "description": "BCM GPIO pin number (e.g. 17, 18, 22, 23, 24, 25, 27)"
                    },
                    "value": {
                        "type": "boolean",
                        "description": "true = HIGH (3.3V, on), false = LOW (0V, off)"
                    }
                },
                "required": ["pin", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "screen_display",
            "description": "Display text content on the connected screen. The screen may be an OLED, LCD, or HDMI display depending on hardware configuration.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Text content to display. Keep short if using a small OLED display (max ~20 chars per line)."
                    },
                    "clear": {
                        "type": "boolean",
                        "description": "Whether to clear the screen before writing new content. Defaults to true."
                    }
                },
                "required": ["content"]
            }
        }
    }
]
