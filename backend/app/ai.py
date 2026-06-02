import base64
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
    reasoning_tokens: int = 0


@dataclass
class ImageGenerationResult:
    images: list[str]
    revised_prompt: Optional[str] = None
    text_content: str = ""


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

    @abstractmethod
    async def generate_image(self, prompt: str, model_config, size: str = "1024x1024", n: int = 1) -> ImageGenerationResult:
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
                content = m.get("content", "")
                if isinstance(content, list):
                    parts = []
                    for part in content:
                        if part.get("type") == "image_url":
                            parts.append({
                                "type": "image_url",
                                "image_url": part["image_url"],
                            })
                        elif part.get("type") == "text":
                            parts.append({"type": "text", "text": part["text"]})
                    entry["content"] = parts
                else:
                    entry["content"] = content or ""
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
        if getattr(model_config, "thinking_enabled", False):
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
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
            reasoning_content=getattr(msg, "reasoning_content", "") or getattr(msg, "reasoning", "") or "",
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
        if getattr(model_config, "thinking_enabled", False):
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
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
            reasoning_content=getattr(msg, "reasoning_content", "") or getattr(msg, "reasoning", "") or "",
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
        if getattr(model_config, "thinking_enabled", False):
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        openai_tools = build_tool_specs(tools)
        if openai_tools:
            kwargs["tools"] = openai_tools
            kwargs["tool_choice"] = "auto"

        stream = await client.chat.completions.create(**kwargs)

        tool_call_accumulator: dict[int, dict] = {}
        finish_reason = None
        usage = None
        reasoning_tokens = 0

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            if delta and delta.content:
                yield StreamChunk(content_delta=delta.content)

            reasoning_delta = (
                getattr(delta, "reasoning_content", None)
                or getattr(delta, "reasoning", None)
            )
            if delta and reasoning_delta:
                yield StreamChunk(reasoning_content_delta=reasoning_delta)

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
                reasoning_tokens = getattr(chunk.usage, "completion_tokens_details", None)
                if reasoning_tokens:
                    reasoning_tokens = getattr(reasoning_tokens, "reasoning_tokens", 0) or 0

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
            reasoning_tokens=reasoning_tokens,
        )

    async def stream_chat(self, messages: list[dict], tools: list[ToolDef], model_config) -> AsyncGenerator[StreamChunk, None]:
        async for chunk in self._stream_openai(messages, tools, model_config):
            yield chunk

    async def stream_chat_with_results(self, messages: list[dict], tools: list[ToolDef], model_config, tool_results: list[dict]) -> AsyncGenerator[StreamChunk, None]:
        async for chunk in self._stream_openai(messages, tools, model_config):
            yield chunk

    async def generate_image(self, prompt: str, model_config, size: str = "1024x1024", n: int = 1) -> ImageGenerationResult:
        client = await self._get_client(model_config)
        base_url = (model_config.base_url or "").lower()
        if "openrouter" in base_url:
            image_config = {"image_size": "1K"}
            size_to_config = {
                "1024x1024": {"aspect_ratio": "1:1", "image_size": "1K"},
                "1792x1024": {"aspect_ratio": "16:9", "image_size": "1K"},
                "1024x1792": {"aspect_ratio": "9:16", "image_size": "1K"},
            }
            if size in size_to_config:
                image_config = size_to_config[size]
            response = await client.chat.completions.create(
                model=model_config.model_name,
                messages=[{"role": "user", "content": prompt}],
                stream=False,
                extra_body={"modalities": ["image"], "image_config": image_config},
            )
        else:
            response = await client.images.generate(
                model=model_config.model_name,
                prompt=prompt,
                size=size,
                n=n,
            )
        if hasattr(response, "data"):
            images = [img.url or img.b64_json or "" for img in response.data]
            revised = getattr(response, "revised_prompt", None) or None
            return ImageGenerationResult(images=images, revised_prompt=revised)
        msg = response.choices[0].message
        images = []
        raw_images = getattr(msg, "images", None) or []
        for img in raw_images:
            if hasattr(img, "image_url") and img.image_url:
                url = getattr(img.image_url, "url", "") or ""
                if url:
                    images.append(url)
            elif isinstance(img, dict):
                iu = img.get("image_url", {})
                u = iu.get("url", "") if isinstance(iu, dict) else ""
                if u:
                    images.append(u)
        return ImageGenerationResult(images=images, text_content=msg.content or "")


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
            elif isinstance(m.get("content"), list):
                content_parts = []
                for part in m["content"]:
                    if part.get("type") == "image_url":
                        iu = part.get("image_url", {})
                        url = iu.get("url", "")
                        if url.startswith("data:"):
                            header, b64data = url.split(",", 1)
                            media_type = header.split(":")[1].split(";")[0] if ":" in header else "image/png"
                            content_parts.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64data,
                                },
                            })
                    elif part.get("type") == "text":
                        content_parts.append({"type": "text", "text": part["text"]})
                converted.append({"role": m["role"], "content": content_parts})
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
        if getattr(model_config, "thinking_enabled", False):
            budget = getattr(model_config, "thinking_budget_tokens", None) or 4000
            max_tok = model_config.max_tokens or 4096
            if budget >= max_tok:
                raise ValueError(f"thinking_budget_tokens ({budget}) must be less than max_tokens ({max_tok})")
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}

        response = await client.messages.create(**kwargs)

        content = ""
        tool_calls = []
        reasoning_content = ""
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input if isinstance(block.input, dict) else {},
                })
            elif block.type == "thinking":
                reasoning_content = getattr(block, "thinking", "")
            elif block.type == "redacted_thinking":
                reasoning_content = "[Thinking redacted by provider]"

        return AIResponse(content=content, tool_calls=tool_calls, reasoning_content=reasoning_content)

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
        thinking_enabled = getattr(model_config, "thinking_enabled", False)
        if thinking_enabled:
            budget = getattr(model_config, "thinking_budget_tokens", None) or 4000
            max_tok = model_config.max_tokens or 4096
            if budget >= max_tok:
                raise ValueError(f"thinking_budget_tokens ({budget}) must be less than max_tokens ({max_tok})")
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}

        accumulated_thinking = ""
        accumulated_signature = ""

        async with client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    delta_type = getattr(event.delta, "type", None)
                    if delta_type == "text_delta":
                        text = getattr(event.delta, "text", "") or ""
                        if text.strip():
                            yield StreamChunk(content_delta=text)
                    elif delta_type == "thinking_delta":
                        thinking_text = getattr(event.delta, "thinking", "") or ""
                        accumulated_thinking += thinking_text
                        yield StreamChunk(reasoning_content_delta=thinking_text)
                    elif delta_type == "signature_delta":
                        accumulated_signature += getattr(event.delta, "signature", "") or ""

            final = stream.get_final_message()

            tool_calls = []
            reasoning_content = ""
            for block in final.content:
                if block.type == "tool_use":
                    tool_calls.append({
                        "id": block.id,
                        "name": block.name,
                        "arguments": block.input if isinstance(block.input, dict) else {},
                    })
                elif block.type == "thinking":
                    reasoning_content = getattr(block, "thinking", "") or ""
                    accumulated_signature = getattr(block, "signature", "") or ""
                elif block.type == "redacted_thinking":
                    reasoning_content = "[Thinking redacted by provider]"

            if reasoning_content and not accumulated_thinking:
                accumulated_thinking = reasoning_content
                yield StreamChunk(reasoning_content_delta=reasoning_content)

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

    async def generate_image(self, prompt: str, model_config, size: str = "1024x1024", n: int = 1) -> ImageGenerationResult:
        raise NotImplementedError("Image generation is not supported on the Anthropic provider. Use an OpenAI-compatible provider.")


_providers: dict[str, AIProvider] = {
    "openai_compatible": OpenAICompatibleProvider(),
    "anthropic": AnthropicProvider(),
}


def get_provider(provider_type: str) -> Optional[AIProvider]:
    return _providers.get(provider_type)
