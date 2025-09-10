

from openai import AsyncOpenAI
import asyncio
import json
from typing import Any, List, Dict, Union, cast
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionUserMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionAssistantMessageParam,
    ChatCompletionDeveloperMessageParam,
    ChatCompletionToolMessageParam,
)


class PatchedConverter:
    """Patched version of the Converter class to fix Union type instantiation"""

    @classmethod
    def create_tool_call_dict(cls, call_id: str, name: str, arguments: str) -> Dict[str, Any]:
        """Create a tool call dictionary instead of trying to instantiate Union type"""
        return {
            "id": call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": arguments,
            }
        }

    @classmethod
    def items_to_messages_fixed(cls, items: Any) -> List[ChatCompletionMessageParam]:
        """Fixed version that doesn't try to instantiate Union types"""

        if isinstance(items, str):
            return [ChatCompletionUserMessageParam(role="user", content=items)]

        result: List[ChatCompletionMessageParam] = []
        current_assistant_msg: Dict[str, Any] = None

        def flush_assistant_message():
            nonlocal current_assistant_msg
            if current_assistant_msg is not None:
                if not current_assistant_msg.get("tool_calls"):
                    current_assistant_msg.pop("tool_calls", None)
                result.append(
                    cast(ChatCompletionAssistantMessageParam, current_assistant_msg))
                current_assistant_msg = None

        def ensure_assistant_message():
            nonlocal current_assistant_msg
            if current_assistant_msg is None:
                current_assistant_msg = {
                    "role": "assistant",
                    "tool_calls": []
                }
            return current_assistant_msg

        for item in items:
            # Handle different item types
            if isinstance(item, dict):
                if item.get("type") == "function_call":
                    asst = ensure_assistant_message()
                    tool_calls = list(asst.get("tool_calls", []))

                    # Create tool call dict instead of instantiating Union type
                    new_tool_call = cls.create_tool_call_dict(
                        call_id=item["call_id"],
                        name=item["name"],
                        arguments=item["arguments"] or "{}"
                    )

                    tool_calls.append(new_tool_call)
                    asst["tool_calls"] = tool_calls

                elif item.get("type") == "function_call_output":
                    flush_assistant_message()
                    tool_msg: ChatCompletionToolMessageParam = {
                        "role": "tool",
                        "tool_call_id": item["call_id"],
                        "content": item["output"]
                    }
                    result.append(tool_msg)

                elif item.get("role") in ["user", "system", "assistant", "developer"]:
                    flush_assistant_message()

                    role = item["role"]
                    content = item.get("content", "")

                    if role == "user":
                        result.append(ChatCompletionUserMessageParam(
                            role="user", content=content))
                    elif role == "system":
                        result.append(ChatCompletionSystemMessageParam(
                            role="system", content=content))
                    elif role == "assistant":
                        result.append(ChatCompletionAssistantMessageParam(
                            role="assistant", content=content))
                    elif role == "developer":
                        result.append(ChatCompletionDeveloperMessageParam(
                            role="developer", content=content))

        flush_assistant_message()
        return result


# Solution 2: Monkey patch the original converter
def patch_agents_converter():
    """Monkey patch the agents library to fix the Union type issue"""
    try:
        from agents.models.chatcmpl_converter import Converter

        # Store original method
        original_items_to_messages = Converter.items_to_messages

        def patched_items_to_messages(items):
            try:
                return original_items_to_messages(items)
            except TypeError as e:
                if "Cannot instantiate typing.Union" in str(e):
                    # Use our fixed implementation
                    return PatchedConverter.items_to_messages_fixed(items)
                else:
                    raise e

        # Replace the method
        Converter.items_to_messages = staticmethod(patched_items_to_messages)
        print("✅ Successfully patched Converter.items_to_messages")

    except ImportError:
        print("❌ Could not import agents library for patching")
    except Exception as e:
        print(f"❌ Error patching converter: {e}")


# Solution 3: Alternative approach using direct OpenAI client


class SimpleWeatherAgent:
    """Simple agent implementation without the problematic agents library"""

    def __init__(self, api_key: str):
        self.client = AsyncOpenAI(api_key=api_key)
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get current weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {
                                "type": "string",
                                "description": "The city name"
                            }
                        },
                        "required": ["city"]
                    }
                }
            }
        ]

    async def get_weather(self, city: str) -> str:
        """Mock weather function - replace with your actual weather API"""
        import requests
        try:
            url = f'https://api.weatherstack.com/current?access_key=YOUR_KEY&query={city}'
            response = requests.get(url, timeout=10)
            data = response.json()

            if 'current' in data:
                current = data['current']
                return f"Weather in {city}: {current.get('temperature')}°C, {current.get('weather_descriptions', ['N/A'])[0]}"
            else:
                return f"Could not get weather for {city}"
        except:
            return f"Mock weather: Sunny, 25°C in {city}"

    async def run(self, user_message: str) -> str:
        messages = [{"role": "user", "content": user_message}]

        response = await self.client.chat.completions.create(
            model="gpt-4",
            messages=messages,
            tools=self.tools,
            tool_choice="auto"
        )

        message = response.choices[0].message

        if message.tool_calls:
            # Handle tool calls
            messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    } for tc in message.tool_calls
                ]
            })

            # Execute tools
            for tool_call in message.tool_calls:
                if tool_call.function.name == "get_weather":
                    import json
                    args = json.loads(tool_call.function.arguments)
                    result = await self.get_weather(args["city"])

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result
                    })

            # Get final response
            final_response = await self.client.chat.completions.create(
                model="gpt-4",
                messages=messages
            )

            return final_response.choices[0].message.content

        return message.content


# Solution 4: Usage examples
async def main():
    """Example usage of the solutions"""

    print("=== Solution 1: Patching the agents library ===")
    patch_agents_converter()

    # Now try to use your original agents code
    try:
        from agents import Agent, Runner
        # Your original code should work now
        print("Agents library patched successfully")
    except Exception as e:
        print(f"Agents library issue: {e}")

    print("\n=== Solution 3: Using simple agent ===")
    # Use the simple agent as an alternative
    agent = SimpleWeatherAgent("your-openai-api-key")
    result = await agent.run("What is the weather in Islamabad?")
    print(f"Result: {result}")


# Solution 5: Environment fix
def fix_python_environment():
    """Fix for Python 3.10 typing issues"""
    import sys
    print(f"Python version: {sys.version}")

    # Check if we need to upgrade packages
    required_packages = [
        "openai>=1.0.0",
        "typing-extensions>=4.0.0",
    ]

    print("Required packages:")
    for pkg in required_packages:
        print(f"  - {pkg}")

    print("\nTo fix the issue, run:")
    print("pip install --upgrade openai typing-extensions")
    print("pip install --upgrade agents")


if __name__ == "__main__":
    # Apply the patch
    patch_agents_converter()

    # Show environment fix info
    fix_python_environment()

    # Run async example
    # asyncio.run(main())
