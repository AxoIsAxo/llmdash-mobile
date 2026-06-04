import base64
import json
import re
from typing import Optional, AsyncGenerator
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic

from . import config as app_config


# Hermes/Qwen/mimo style inline tool call tags.
# Some models (notably Xiaomi MiMo) emit `<tool_call>{...}</tool_call>` text in
# their content stream instead of using the OpenAI tool_calls API. This breaks
# the chat UX and causes malformed output. We detect/strip these blocks and
# convert them to real tool calls.
_INLINE_TOOL_CALL_OPEN = "<tool_call>"
_INLINE_TOOL_CALL_CLOSE = "</tool_call>"
_INLINE_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL
)

# XML-attribute style inline tool call tags, used by some Anthropic-style
# proxies, the Xiaomi MiMo family and a handful of other chat models:
#   <function=NAME>
#   <parameter=KEY>
#   VALUE
#   <parameter=KEY2>
#   VALUE2
#   </function>
# We also accept the `<invoke name="...">` and `<parameter name="...">` forms.
#
# Each tag has two opener variants:
#   1. <function=NAME>      / <parameter=KEY>
#   2. <invoke name="NAME"> / <parameter name="KEY">
# Both forms accept the value quoted with " or ' or unquoted.
#
# Group layout: (1,2,3) carry the first-form value, (4,5,6) carry the
# second-form value. Whichever was populated is the actual tag value.
_XML_FUNC_OPEN_RE = re.compile(
    r"<(?:function|antml:function)\s*=\s*"
    r"(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))"
    r"\s*>"
    r"|"
    r"<(?:invoke|antml:invoke)\s+name\s*=\s*"
    r"(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))"
    r"\s*>"
)
_XML_FUNC_CLOSE_RE = re.compile(
    r"</(?:function|antml:function|antml:invoke|invoke)>"
)
_XML_PARAM_RE = re.compile(
    r"<(?:parameter|antml:param|antml:parameter)\s*=\s*"
    r"(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))"
    r"\s*>"
    r"|"
    r"<(?:parameter|antml:param|antml:parameter)\s+name\s*=\s*"
    r"(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))"
    r"\s*>"
)
# A parameter-value terminator: either the next parameter tag, the function
# close tag, or a parameter close tag (used by some models).
_XML_PARAM_END_RE = re.compile(
    r"<(?:parameter|antml:param|antml:parameter)"
    r"|</(?:parameter|antml:param|antml:parameter|function|antml:function|invoke|antml:invoke)>"
)


def _xml_tag_value(m: "re.Match[str]") -> str:
    for i in range(1, m.lastindex + 1 if m.lastindex else 7):
        v = m.group(i)
        if v:
            return v
    return ""


def _parse_inline_tool_call(json_str: str, fallback_id: str) -> Optional[dict]:
    if not json_str or not json_str.strip():
        return None
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name") or data.get("function") or data.get("tool")
    if not isinstance(name, str) or not name:
        return None
    args = data.get("arguments", data.get("parameters", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
        except TypeError:
            args = {}
    if not isinstance(args, dict):
        args = {}
    return {"id": fallback_id, "name": name, "arguments": args}


def _parse_xml_attr_tool_call(
    func_name: str, body: str, fallback_id: str
) -> Optional[dict]:
    if not func_name:
        return None
    params: dict = {}
    pos = 0
    while pos < len(body):
        m = _XML_PARAM_RE.search(body, pos)
        if not m:
            break
        key = _xml_tag_value(m).strip()
        value_start = m.end()
        # Value extends until the next parameter tag, a parameter-close tag,
        # or the function-close tag.
        end_m = _XML_PARAM_END_RE.search(body, value_start)
        if end_m:
            value = body[value_start : end_m.start()]
            pos = end_m.start()
        else:
            value = body[value_start:]
            pos = len(body)
        # Trim a single leading newline (model often inserts one for
        # readability) plus any trailing whitespace.
        if value.startswith("\n"):
            value = value[1:]
        elif value.startswith("\r\n"):
            value = value[2:]
        params[key] = value.rstrip()
    if not params:
        return None
    return {"id": fallback_id, "name": func_name, "arguments": params}


def _extract_inline_tool_calls(content: str, id_prefix: str = "call_text") -> list[dict]:
    out: list[dict] = []
    consumed_spans: list[tuple[int, int]] = []

    # Hermes/Qwen/mimo JSON format: <tool_call>{...}</tool_call>
    for i, match in enumerate(_INLINE_TOOL_CALL_RE.finditer(content)):
        tc = _parse_inline_tool_call(match.group(1), f"{id_prefix}_{i}")
        if tc:
            out.append(tc)
            consumed_spans.append(match.span())

    # XML-attribute format: <function=NAME>...<parameter=KEY>VALUE</function>
    i = len(out)
    pos = 0
    while pos < len(content):
        open_m = _XML_FUNC_OPEN_RE.search(content, pos)
        if not open_m:
            break
        func_name = _xml_tag_value(open_m)
        # Find the matching close tag
        close_m = _XML_FUNC_CLOSE_RE.search(content, open_m.end())
        if not close_m:
            break
        body = content[open_m.end() : close_m.start()]
        tc = _parse_xml_attr_tool_call(
            func_name, body, f"{id_prefix}_{i}"
        )
        if tc:
            out.append(tc)
            consumed_spans.append((open_m.start(), close_m.end()))
            i += 1
        pos = close_m.end()

    return out


def _strip_inline_tool_calls(content: str) -> str:
    # Strip JSON-style blocks first
    content = _INLINE_TOOL_CALL_RE.sub("", content)
    # Then strip XML-attribute blocks. Walk through and keep only the gaps.
    out_parts: list[str] = []
    pos = 0
    while pos < len(content):
        open_m = _XML_FUNC_OPEN_RE.search(content, pos)
        if not open_m:
            out_parts.append(content[pos:])
            break
        out_parts.append(content[pos : open_m.start()])
        close_m = _XML_FUNC_CLOSE_RE.search(content, open_m.end())
        if not close_m:
            # Unterminated block, emit the opener and stop
            out_parts.append(content[open_m.start() :])
            break
        pos = close_m.end()
    return "".join(out_parts)


def _has_inline_tool_call(content: str) -> bool:
    if not content:
        return False
    if _INLINE_TOOL_CALL_RE.search(content):
        return True
    if _XML_FUNC_OPEN_RE.search(content) and _XML_FUNC_CLOSE_RE.search(content):
        return True
    return False


class _InlineToolCallFilter:
    """Stateful filter that strips inline tool call text from a streamed
    content stream and yields the parsed tool calls.

    Handles two common inline formats produced by models that don't use the
    OpenAI tool_calls API (e.g. Xiaomi MiMo, certain Hermes/Qwen builds and
    some Anthropic-SDK-style proxies):

      1. JSON-wrapped:  <tool_call>{...}</tool_call>
      2. XML-attribute: <function=NAME>...<parameter=KEY>VALUE...</function>
                       (also `<invoke name="...">...</invoke>` variants)

    Both filters run on every chunk. If a tag straddles several deltas, the
    filter buffers it. When the stream ends with an unterminated tag, the
    buffered text is emitted verbatim so the user still sees it.
    """

    def __init__(self) -> None:
        self._json = _JsonInlineToolCallFilter()
        self._xml = _XmlAttrToolCallFilter()

    def feed(self, chunk: str) -> tuple[str, list[dict]]:
        if not chunk:
            return "", []
        out1, calls1 = self._json.feed(chunk)
        out2, calls2 = self._xml.feed(out1)
        return out2, calls1 + calls2

    def flush(self) -> str:
        return self._json.flush() + self._xml.flush()

    @property
    def collected(self) -> list[dict]:
        return self._json.collected + self._xml.collected


class _JsonInlineToolCallFilter:
    """Stateful filter for the JSON-wrapped `<tool_call>{...}</tool_call>` form."""

    _OPEN = _INLINE_TOOL_CALL_OPEN
    _CLOSE = _INLINE_TOOL_CALL_CLOSE

    def __init__(self) -> None:
        self._state = "normal"
        self._tag_buf = ""
        self._idx = 0
        self._collected: list[dict] = []

    def _emit_tag_completion(self) -> Optional[dict]:
        buf = self._tag_buf
        self._tag_buf = ""
        self._state = "normal"
        json_str = buf[: -len(self._CLOSE)]
        tc = _parse_inline_tool_call(json_str, f"call_text_{self._idx}")
        if tc is not None:
            self._idx += 1
            self._collected.append(tc)
            return tc
        return None

    def feed(self, chunk: str) -> tuple[str, list[dict]]:
        if not chunk:
            return "", []
        out: list[str] = []
        new_calls: list[dict] = []
        i = 0
        n = len(chunk)
        while i < n:
            c = chunk[i]
            if self._state == "normal":
                if c == "<":
                    self._state = "tag_start"
                    self._tag_buf = "<"
                else:
                    out.append(c)
                i += 1
                continue

            if self._state == "tag_start":
                self._tag_buf += c
                if self._tag_buf == self._OPEN:
                    self._state = "in_tool_call"
                    self._tag_buf = ""
                elif not self._OPEN.startswith(self._tag_buf):
                    out.append(self._tag_buf)
                    self._tag_buf = ""
                    self._state = "normal"
                i += 1
                continue

            # in_tool_call
            self._tag_buf += c
            if self._tag_buf.endswith(self._CLOSE):
                tc = self._emit_tag_completion()
                if tc is not None:
                    new_calls.append(tc)
            i += 1
        return "".join(out), new_calls

    def flush(self) -> str:
        if not self._tag_buf:
            return ""
        if self._state == "tag_start":
            out = self._tag_buf
        elif self._state == "in_tool_call":
            out = f"{self._OPEN}{self._tag_buf}"
        else:
            out = ""
        self._tag_buf = ""
        self._state = "normal"
        return out

    @property
    def collected(self) -> list[dict]:
        return self._collected


class _XmlAttrToolCallFilter:
    """Stateful filter for the XML-attribute inline tool call form:

        <function=NAME>
        <parameter=KEY>
        VALUE
        </function>

    (also accepts `<invoke name="...">` and the `antml:` namespaced variants).
    Buffers content until a complete block is visible, then emits the parsed
    tool call. Unterminated blocks at end-of-stream are flushed verbatim.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._idx = 0
        self._collected: list[dict] = []

    @staticmethod
    def _partial_opener_len(buf: str) -> int:
        """Return how many trailing characters of `buf` to keep as a possible
        partial opener. A '<' is only treated as a candidate opener start if
        it is at the very end of `buf` (we don't know the next char yet) or
        is followed by an alphabetic character (a real tag would be
        `<function=...>`, `<invoke ...>`, `<antml:...>`, etc.). Bare '<' in
        math/comparisons like `a < b` is followed by whitespace and is not a
        candidate, so we don't buffer.
        """
        last_lt = buf.rfind("<")
        if last_lt == -1:
            return 0
        # The '<' is the very last char: hold it (waiting for next char).
        if last_lt == len(buf) - 1:
            return 1
        # Otherwise, only hold if followed by an alpha char.
        if not buf[last_lt + 1].isalpha():
            return 0
        return len(buf) - last_lt

    def feed(self, chunk: str) -> tuple[str, list[dict]]:
        if chunk:
            self._buf += chunk
        out: list[str] = []
        new_calls: list[dict] = []

        while True:
            open_m = _XML_FUNC_OPEN_RE.search(self._buf)
            if not open_m:
                keep = self._partial_opener_len(self._buf)
                if keep and len(self._buf) > keep:
                    out.append(self._buf[:-keep])
                    self._buf = self._buf[-keep:]
                elif not keep and self._buf:
                    out.append(self._buf)
                    self._buf = ""
                break

            # Emit everything before the opener
            out.append(self._buf[: open_m.start()])
            # Try to find the matching close
            close_m = _XML_FUNC_CLOSE_RE.search(self._buf, open_m.end())
            if not close_m:
                # Incomplete: keep from the opener onward in the buffer so we
                # can finish it on the next chunk.
                self._buf = self._buf[open_m.start():]
                break

            # We have a complete block: opener at open_m, closer at close_m.
            func_name = _xml_tag_value(open_m)
            body = self._buf[open_m.end() : close_m.start()]
            tc = _parse_xml_attr_tool_call(
                func_name, body, f"call_xml_{self._idx}"
            )
            if tc is not None:
                self._idx += 1
                self._collected.append(tc)
                new_calls.append(tc)
            # Drop the entire block from the buffer and loop
            self._buf = self._buf[close_m.end():]

        return "".join(out), new_calls

    def flush(self) -> str:
        if not self._buf:
            return ""
        out = self._buf
        self._buf = ""
        return out

    @property
    def collected(self) -> list[dict]:
        return self._collected


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
        thinking_requested = False
        if getattr(model_config, "thinking_enabled", False):
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            thinking_requested = True
        openai_tools = build_tool_specs(tools)
        tools_sent = False
        if openai_tools:
            kwargs["tools"] = openai_tools
            kwargs["tool_choice"] = "auto"
            tools_sent = True

        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as e:
            error_str = str(e).lower()
            if tools_sent and ("support tool" in error_str or "tool use" in error_str or "function call" in error_str or "tool_choice" in error_str or "tools" in error_str):
                return await self.chat(messages, [], model_config)
            if thinking_requested and ("thinking" in error_str or "reasoning" in error_str):
                kwargs.pop("extra_body", None)
                try:
                    response = await client.chat.completions.create(**kwargs)
                except Exception as e2:
                    error_str2 = str(e2).lower()
                    if tools_sent and ("support tool" in error_str2 or "tool use" in error_str2 or "tool_choice" in error_str2 or "tools" in error_str2):
                        return await self.chat(messages, [], model_config)
                    raise e2
            else:
                raise e
        msg = response.choices[0].message

        tool_calls, content = self._extract_tool_calls(msg)

        return AIResponse(
            content=content,
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
        thinking_requested = False
        if getattr(model_config, "thinking_enabled", False):
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            thinking_requested = True
        tools_sent = False
        if openai_tools:
            kwargs["tools"] = openai_tools
            tools_sent = True

        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as e:
            error_str = str(e).lower()
            if tools_sent and ("support tool" in error_str or "tool use" in error_str or "function call" in error_str or "tool_choice" in error_str or "tools" in error_str):
                return await self.chat_with_results(messages, [], model_config, tool_results)
            if thinking_requested and ("thinking" in error_str or "reasoning" in error_str):
                kwargs.pop("extra_body", None)
                try:
                    response = await client.chat.completions.create(**kwargs)
                except Exception as e2:
                    error_str2 = str(e2).lower()
                    if tools_sent and ("support tool" in error_str2 or "tool use" in error_str2 or "tool_choice" in error_str2 or "tools" in error_str2):
                        return await self.chat_with_results(messages, [], model_config, tool_results)
                    raise e2
            else:
                raise e
        msg = response.choices[0].message

        tool_calls, content = self._extract_tool_calls(msg)

        return AIResponse(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=getattr(msg, "reasoning_content", "") or getattr(msg, "reasoning", "") or "",
        )

    @staticmethod
    def _extract_tool_calls(msg) -> tuple[list[dict], str]:
        """Pull tool calls out of an OpenAI message, falling back to inline
        `<tool_call>...</tool_call>` text for models that don't use the
        tool_calls API (e.g. mimo v2.5). Returns (tool_calls, cleaned_content).
        """
        tool_calls: list[dict] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args,
                })

        content = msg.content or ""
        if not tool_calls:
            inline = _extract_inline_tool_calls(content)
            if inline:
                tool_calls = inline
                content = _strip_inline_tool_calls(content)
        else:
            # API returned proper tool calls — still strip any stray
            # `<tool_call>` text fragments so the user never sees them.
            if _INLINE_TOOL_CALL_RE.search(content):
                content = _strip_inline_tool_calls(content)
        return tool_calls, content

    async def _stream_openai(self, messages: list[dict], tools: list[ToolDef], model_config, retry_without_tools: bool = True, retry_without_thinking: bool = True) -> AsyncGenerator[StreamChunk, None]:
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
        thinking_requested = False
        if getattr(model_config, "thinking_enabled", False):
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            thinking_requested = True
        openai_tools = build_tool_specs(tools)
        tools_sent = False
        if openai_tools:
            kwargs["tools"] = openai_tools
            kwargs["tool_choice"] = "auto"
            tools_sent = True

        stream = None
        try:
            stream = await client.chat.completions.create(**kwargs)
        except Exception as e:
            error_str = str(e).lower()
            if retry_without_tools and tools_sent and ("support tool" in error_str or "tool use" in error_str or "function call" in error_str or "tool_choice" in error_str or "tools" in error_str):
                async for chunk in self._stream_openai(messages, [], model_config, retry_without_tools=False, retry_without_thinking=retry_without_thinking):
                    yield chunk
                return
            if retry_without_thinking and thinking_requested and ("thinking" in error_str or "reasoning" in error_str):
                kwargs.pop("extra_body", None)
                try:
                    stream = await client.chat.completions.create(**kwargs)
                except Exception as e2:
                    error_str2 = str(e2).lower()
                    if retry_without_tools and tools_sent and ("support tool" in error_str2 or "tool use" in error_str2 or "function call" in error_str2 or "tool_choice" in error_str2 or "tools" in error_str2):
                        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
                        async for chunk in self._stream_openai(messages, [], model_config, retry_without_tools=False, retry_without_thinking=False):
                            yield chunk
                        return
                    raise e2
            else:
                raise e

        if stream is None:
            return

        tool_call_accumulator: dict[int, dict] = {}
        inline_tool_call_filter = _InlineToolCallFilter()
        finish_reason = None
        usage = None
        reasoning_tokens = 0

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            if delta and delta.content:
                cleaned, inline_calls = inline_tool_call_filter.feed(delta.content)
                if cleaned:
                    yield StreamChunk(content_delta=cleaned)
                if inline_calls:
                    pass  # collected on filter; emitted after stream end

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

        flushed = inline_tool_call_filter.flush()
        if flushed:
            yield StreamChunk(content_delta=flushed)

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

        # If the API did not return proper tool_calls, fall back to tool calls
        # extracted from inline `<tool_call>...</tool_call>` text emitted by
        # models that don't use the OpenAI tool_calls API (e.g. mimo v2.5).
        if not accumulated_tool_calls:
            inline_calls = inline_tool_call_filter.collected
            if inline_calls:
                accumulated_tool_calls = inline_calls

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
