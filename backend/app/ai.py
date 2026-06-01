import json
from typing import Optional, AsyncGenerator
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic

from . import config as app_config


class ToolDef:
    def __init__(self, name: str, description: str, input_schema: dict):
        self.name = name
        self.description = description
        self.input_schema = input_schema


class AIResponse:
    def __init__(self, content: str = "", tool_calls: Optional[list[dict]] = None, reasoning_content: str = ""):
        self.content = content or ""
        self.tool_calls = tool_calls or []
        self.reasoning_content = reasoning_content


@dataclass
class StreamChunk:
    content_delta: str = ""
    reasoning_content_delta: str = ""
    tool_calls: Optional[list[dict]] = None
    finish_reason: Optional[str] = None
    usage: Optional[dict] = None


class AIProvider(ABC):
    @abstractmethod
    async def chat(self, messages: list[dict], tools: list[ToolDef], model_config) -> AIResponse:
        ...

    @abstractmethod
    async def chat_with_results(self, messages: list[dict], tools: list[ToolDef], model_config, tool_results: list[dict]) -> AIResponse:
        ...

    @abstractmethod
    async def stream_chat(self, messages: list[dict], tools: list[ToolDef], model_config) -> AsyncGenerator[StreamChunk, None]:
        ...

    @abstractmethod
    async def stream_chat_with_results(self, messages: list[dict], tools: list[ToolDef], model_config, tool_results: list[dict]) -> AsyncGenerator[StreamChunk, None]:
        ...


def build_tool_specs(tools: list[ToolDef]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


class OpenAICompatibleProvider(AIProvider):
    async def _get_client(self, model_config):
        api_key = None
        if model_config.api_key_env:
            api_key = getattr(app_config.settings, model_config.api_key_env.lower(), None) or ""
        else:
            api_key = ""
        base_url = model_config.base_url or "https://api.openai.com/v1"
        return AsyncOpenAI(api_key=api_key, base_url=base_url)

    def _convert_messages(self, messages: list[dict]) -> list[dict]:
        converted = []
        for m in messages:
            entry = {"role": m["role"]}
            if m["role"] == "assistant":
                has_tools = bool(m.get("tool_calls_json"))
                entry["content"] = (m.get("content") or None) if not has_tools else None
                if m.get("reasoning_content"):
                    entry["reasoning_content"] = m["reasoning_content"]
                if has_tools:
                    tcs = json.loads(m["tool_calls_json"]) if isinstance(m["tool_calls_json"], str) else m["tool_calls_json"]
                    entry["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"]),
                            },
                        }
                        for tc in tcs
                    ]
            elif m["role"] == "tool":
                entry["tool_call_id"] = m.get("tool_call_id", "")
                entry["content"] = m.get("content", "")
            else:
                entry["content"] = m.get("content", "")
            converted.append(entry)
        return converted

    async def chat(self, messages: list[dict], tools: list[ToolDef], model_config) -> AIResponse:
        client = await self._get_client(model_config)
        openai_messages = self._convert_messages(messages)
        kwargs = {
            "model": model_config.model_name,
            "messages": openai_messages,
            "temperature": model_config.temperature or 0.7,
            "max_tokens": model_config.max_tokens or 4096,
        }
        openai_tools = build_tool_specs(tools)
        if openai_tools:
            kwargs["tools"] = openai_tools
            kwargs["tool_choice"] = "auto"

        response = await client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                })

        return AIResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            reasoning_content=getattr(msg, "reasoning_content", "") or "",
        )

    async def chat_with_results(self, messages: list[dict], tools: list[ToolDef], model_config, tool_results: list[dict]) -> AIResponse:
        client = await self._get_client(model_config)
        openai_messages = self._convert_messages(messages)
        openai_tools = build_tool_specs(tools)
        kwargs = {
            "model": model_config.model_name,
            "messages": openai_messages,
            "temperature": model_config.temperature or 0.7,
            "max_tokens": model_config.max_tokens or 4096,
        }
        if openai_tools:
            kwargs["tools"] = openai_tools

        response = await client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                })

        return AIResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            reasoning_content=getattr(msg, "reasoning_content", "") or "",
        )

    async def _stream_openai(self, messages: list[dict], tools: list[ToolDef], model_config) -> AsyncGenerator[StreamChunk, None]:
        client = await self._get_client(model_config)
        openai_messages = self._convert_messages(messages)
        kwargs = {
            "model": model_config.model_name,
            "messages": openai_messages,
            "temperature": model_config.temperature or 0.7,
            "max_tokens": model_config.max_tokens or 4096,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        openai_tools = build_tool_specs(tools)
        if openai_tools:
            kwargs["tools"] = openai_tools
            kwargs["tool_choice"] = "auto"

        stream = await client.chat.completions.create(**kwargs)

        tool_call_accumulator: dict[int, dict] = {}
        finish_reason = None
        usage = None

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            if delta and delta.content:
                yield StreamChunk(content_delta=delta.content)

            if delta and getattr(delta, "reasoning_content", None):
                yield StreamChunk(reasoning_content_delta=delta.reasoning_content)

            if delta and delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_accumulator:
                        tool_call_accumulator[idx] = {"id": "", "name": "", "arguments_str": ""}
                    if tc.id:
                        tool_call_accumulator[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_call_accumulator[idx]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_call_accumulator[idx]["arguments_str"] += tc.function.arguments

            if chunk.usage:
                usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                }

        accumulated_tool_calls = []
        for tc_data in tool_call_accumulator.values():
            try:
                args = json.loads(tc_data["arguments_str"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            accumulated_tool_calls.append({
                "id": tc_data["id"],
                "name": tc_data["name"],
                "arguments": args,
            })

        yield StreamChunk(
            tool_calls=accumulated_tool_calls if accumulated_tool_calls else None,
            finish_reason=finish_reason,
            usage=usage,
        )

    async def stream_chat(self, messages: list[dict], tools: list[ToolDef], model_config) -> AsyncGenerator[StreamChunk, None]:
        async for chunk in self._stream_openai(messages, tools, model_config):
            yield chunk

    async def stream_chat_with_results(self, messages: list[dict], tools: list[ToolDef], model_config, tool_results: list[dict]) -> AsyncGenerator[StreamChunk, None]:
        async for chunk in self._stream_openai(messages, tools, model_config):
            yield chunk


class AnthropicProvider(AIProvider):
    async def _get_client(self, model_config):
        api_key = getattr(app_config.settings, "anthropic_api_key", None) or ""
        return AsyncAnthropic(api_key=api_key)

    def _convert_messages(self, messages: list[dict]) -> tuple[list[dict], Optional[str]]:
        system = None
        converted = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
                continue
            if m["role"] == "tool":
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": m.get("content", ""),
                    }]
                })
                continue
            if m["role"] == "assistant":
                content_parts = []
                has_tools = bool(m.get("tool_calls_json"))
                if not has_tools and m.get("content"):
                    content_parts.append({"type": "text", "text": m["content"]})
                if has_tools:
                    tcs = json.loads(m["tool_calls_json"]) if isinstance(m["tool_calls_json"], str) else m["tool_calls_json"]
                    for tc in tcs:
                        content_parts.append({
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["name"],
                            "input": tc["arguments"],
                        })
                converted.append({"role": "assistant", "content": content_parts})
                continue
            if isinstance(m.get("content"), str):
                converted.append({"role": m["role"], "content": m["content"]})
            else:
                converted.append(m)
        return converted, system

    def _build_anthropic_tools(self, tools: list[ToolDef]) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

    async def chat(self, messages: list[dict], tools: list[ToolDef], model_config) -> AIResponse:
        client = await self._get_client(model_config)
        converted, system = self._convert_messages(messages)
        anthropic_tools = self._build_anthropic_tools(tools)

        kwargs = {
            "model": model_config.model_name,
            "messages": converted,
            "max_tokens": model_config.max_tokens or 4096,
            "temperature": model_config.temperature or 0.7,
        }
        if system:
            kwargs["system"] = system
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        response = await client.messages.create(**kwargs)

        content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input if isinstance(block.input, dict) else {},
                })

        return AIResponse(content=content, tool_calls=tool_calls)

    async def chat_with_results(self, messages: list[dict], tools: list[ToolDef], model_config, tool_results: list[dict]) -> AIResponse:
        return await self.chat(messages, tools, model_config)

    async def stream_chat(self, messages: list[dict], tools: list[ToolDef], model_config) -> AsyncGenerator[StreamChunk, None]:
        client = await self._get_client(model_config)
        converted, system = self._convert_messages(messages)
        anthropic_tools = self._build_anthropic_tools(tools)

        kwargs = {
            "model": model_config.model_name,
            "messages": converted,
            "max_tokens": model_config.max_tokens or 4096,
            "temperature": model_config.temperature or 0.7,
        }
        if system:
            kwargs["system"] = system
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        async with client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta" and event.delta.text and event.delta.text.strip():
                        yield StreamChunk(content_delta=event.delta.text)

            final = stream.get_final_message()

            tool_calls = []
            for block in final.content:
                if block.type == "tool_use":
                    tool_calls.append({
                        "id": block.id,
                        "name": block.name,
                        "arguments": block.input if isinstance(block.input, dict) else {},
                    })

            usage = None
            if final.usage:
                usage = {
                    "prompt_tokens": final.usage.input_tokens,
                    "completion_tokens": final.usage.output_tokens,
                    "total_tokens": final.usage.input_tokens + final.usage.output_tokens,
                }

            yield StreamChunk(
                tool_calls=tool_calls if tool_calls else None,
                finish_reason=final.stop_reason,
                usage=usage,
            )

    async def stream_chat_with_results(self, messages: list[dict], tools: list[ToolDef], model_config, tool_results: list[dict]) -> AsyncGenerator[StreamChunk, None]:
        async for chunk in self.stream_chat(messages, tools, model_config):
            yield chunk


_providers: dict[str, AIProvider] = {
    "openai_compatible": OpenAICompatibleProvider(),
    "anthropic": AnthropicProvider(),
}


def get_provider(provider_type: str) -> Optional[AIProvider]:
    return _providers.get(provider_type)
