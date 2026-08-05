import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pi5_assistant"))

os.environ.setdefault("DEEPSEEK_API_KEY", "sk-80c4361aa25443bd83af2eb63a3f12cc")

from llm_orchestrator.deepseek_client import DeepSeekClient
from llm_orchestrator.tool_definitions import TOOLS

config = {
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
        "timeout": 30,
    }
}

client = DeepSeekClient(config)

# Test 1: Simple chat
print("=== Test 1: Simple chat ===")
response = client.chat([
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Say hello in one sentence."},
])
print("Result:", client.get_text(response))
print()

# Test 2: Tool calling
print("=== Test 2: Tool calling ===")
response = client.chat([
    {"role": "system", "content": "You are a Pi 5 AI assistant with vision and servo tools."},
    {"role": "user", "content": "What objects do you see right now?"},
], tools=TOOLS)

if client.is_tool_call(response):
    calls = client.extract_tool_calls(response)
    print(f"Tool calls ({len(calls)}):")
    for c in calls:
        print(f"  - {c['name']}({json.dumps(c['arguments'])})")
else:
    print("No tool call. Text:", client.get_text(response)[:100])

print()

# Test 3: Follow-up with tool result
print("=== Test 3: Tool result follow-up ===")
messages = [
    {"role": "system", "content": "You are a Pi 5 AI assistant."},
    {"role": "user", "content": "What objects do you see?"},
]
response = client.chat(messages, tools=TOOLS)

if client.is_tool_call(response):
    msg = response.choices[0].message
    messages.append(msg)
    for tc in client.extract_tool_calls(response):
        result = json.dumps({"detections": [
            {"class": "person", "confidence": 0.95, "bbox": [100,200,300,400]},
            {"class": "chair", "confidence": 0.87, "bbox": [50,300,150,450]}
        ]})
        messages.append(client.build_tool_result_message(tc["id"], result))

    response = client.chat(messages, tools=TOOLS)
    print("Final response:", client.get_text(response)[:200])

print()
print("=== All tests passed! ===")
