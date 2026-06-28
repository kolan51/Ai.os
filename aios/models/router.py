from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator


@dataclass
class ModelResponse:
    content: str
    tool_calls: list[dict]
    finish_reason: str
    usage: dict


class ModelRouter:
    """
    Thin async wrapper over litellm. Supports any provider — Claude, GPT, Ollama, etc.
    Swap model with one config line: model = "ollama/llama3"
    """

    def __init__(self, model: str, temperature: float = 0.7, max_tokens: int = 4096) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
    ) -> ModelResponse:
        import litellm

        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": all_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = await litellm.acompletion(**kwargs)
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                })

        return ModelResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            },
        )

    async def stream(
        self,
        messages: list[dict],
        system: str | None = None,
    ) -> AsyncIterator[str]:
        import litellm

        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        response = await litellm.acompletion(
            model=self.model,
            messages=all_messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
        )
        async for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
