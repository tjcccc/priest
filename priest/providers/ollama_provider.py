from __future__ import annotations

import json
from typing import AsyncGenerator

import httpx

from priest.errors import ProviderError, ProviderTimeoutError
from priest.providers.base import AdapterCallOptions, AdapterResult, AdapterStreamEvent, ProviderAdapter
from priest.schema.request import OutputSpec, PriestConfig, ToolCall

_DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaProvider(ProviderAdapter):
    """Calls the Ollama /api/chat endpoint using httpx async."""

    provider_name = "ollama"

    def __init__(self, base_url: str = _DEFAULT_BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")

    async def complete(
        self,
        messages: list[dict],
        config: PriestConfig,
        output_spec: OutputSpec,
        options: AdapterCallOptions | None = None,
    ) -> AdapterResult:
        payload: dict = {
            "model": config.model,
            "messages": _translate_messages(messages),
            "stream": False,
        }

        if config.max_output_tokens is not None:
            payload.setdefault("options", {})["num_predict"] = config.max_output_tokens

        if output_spec.json_schema is not None:
            payload["format"] = output_spec.json_schema
        elif output_spec.provider_format == "json":
            payload["format"] = "json"

        _apply_tools(payload, options)

        # Merge provider-specific options (e.g. {"think": False} for Qwen3)
        payload.update(config.provider_options)

        timeout = config.timeout_seconds or 60.0

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                    timeout=timeout,
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            raise ProviderTimeoutError("ollama", timeout)
        except httpx.HTTPStatusError as exc:
            raise ProviderError("ollama", f"HTTP {exc.response.status_code}: {exc.response.text}")
        except httpx.RequestError as exc:
            raise ProviderError("ollama", str(exc))

        data = response.json()
        message = data.get("message", {})
        text = message.get("content")
        tool_calls = _parse_tool_calls(message.get("tool_calls"))

        # Ollama returns token counts in the top-level response
        return AdapterResult(
            text=text,
            raw=data,
            finish_reason="tool_calls" if tool_calls else _map_finish_reason(data.get("done_reason")),
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            tool_calls=tool_calls or None,
        )

    async def stream(
        self,
        messages: list[dict],
        config: PriestConfig,
        output_spec: OutputSpec,
        options: AdapterCallOptions | None = None,
    ) -> AsyncGenerator[str, None]:
        async for event in self.stream_events(messages, config, output_spec, options):
            if event.type == "text_delta" and event.text:
                yield event.text

    async def stream_events(
        self,
        messages: list[dict],
        config: PriestConfig,
        output_spec: OutputSpec,
        options: AdapterCallOptions | None = None,
    ) -> AsyncGenerator[AdapterStreamEvent, None]:
        payload: dict = {
            "model": config.model,
            "messages": _translate_messages(messages),
            "stream": True,
        }

        if config.max_output_tokens is not None:
            payload.setdefault("options", {})["num_predict"] = config.max_output_tokens

        if output_spec.json_schema is not None:
            payload["format"] = output_spec.json_schema
        elif output_spec.provider_format == "json":
            payload["format"] = "json"

        _apply_tools(payload, options)
        payload.update(config.provider_options)

        timeout = config.timeout_seconds or 60.0
        tool_call_index = 0

        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/api/chat",
                    json=payload,
                    timeout=timeout,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        message = data.get("message", {})
                        content = message.get("content", "")
                        if content:
                            yield AdapterStreamEvent(type="text_delta", text=content)
                        # Ollama delivers each tool call whole in one chunk.
                        for call in _parse_tool_calls(message.get("tool_calls"), start_index=tool_call_index):
                            yield AdapterStreamEvent(
                                type="tool_call_start", index=tool_call_index, id=call.id, name=call.name
                            )
                            yield AdapterStreamEvent(
                                type="tool_call_end", index=tool_call_index, tool_call=call
                            )
                            tool_call_index += 1
                        if data.get("done"):
                            if data.get("prompt_eval_count") is not None or data.get("eval_count") is not None:
                                yield AdapterStreamEvent(
                                    type="usage",
                                    input_tokens=data.get("prompt_eval_count"),
                                    output_tokens=data.get("eval_count"),
                                )
                            yield AdapterStreamEvent(
                                type="finish",
                                finish_reason="tool_calls" if tool_call_index > 0 else _map_finish_reason(data.get("done_reason")),
                            )
                            break
        except httpx.TimeoutException:
            raise ProviderTimeoutError("ollama", timeout)
        except httpx.HTTPStatusError as exc:
            raise ProviderError("ollama", f"HTTP {exc.response.status_code}: {exc.response.text}")
        except httpx.RequestError as exc:
            raise ProviderError("ollama", str(exc))


def _translate_messages(messages: list[dict]) -> list[dict]:
    """Translate OpenAI-format multimodal content blocks to Ollama's format.

    Ollama uses a top-level 'images' field (list of base64 strings) rather than
    inline content blocks. HTTP/HTTPS image URLs are not supported — raise if encountered.
    """
    result = []
    for msg in messages:
        content = msg.get("content")
        if msg["role"] == "tool":
            # Ollama correlates tool results by tool_name, not call id.
            result.append({"role": "tool", "content": content, "tool_name": msg.get("name")})
        elif msg["role"] == "assistant" and msg.get("tool_calls"):
            # Synthesized call ids are dropped on the wire.
            result.append({
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {"function": {"name": call["name"], "arguments": call.get("arguments", {})}}
                    for call in msg["tool_calls"]
                ],
            })
        elif msg["role"] == "user" and isinstance(content, list):
            text_parts: list[str] = []
            image_b64s: list[str] = []
            for block in content:
                if block.get("type") == "text":
                    text_parts.append(block["text"])
                elif block.get("type") == "image_url":
                    url: str = block["image_url"]["url"]
                    if url.startswith("data:"):
                        image_b64s.append(url.split(",", 1)[1])
                    else:
                        raise ProviderError(
                            "ollama",
                            "Ollama requires base64 images; HTTP/HTTPS URLs are not supported. "
                            "Use ImageInput(path=...) or ImageInput(data=...) instead.",
                        )
            new_msg: dict = {"role": "user", "content": " ".join(text_parts)}
            if image_b64s:
                new_msg["images"] = image_b64s
            result.append(new_msg)
        else:
            result.append(msg)
    return result


def _map_finish_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    mapping = {
        "stop": "stop",
        "length": "length",
        "load": "stop",
    }
    return mapping.get(reason, "unknown")


def _apply_tools(payload: dict, options: AdapterCallOptions | None) -> None:
    """Ollama accepts OpenAI-shaped tools; it has no tool_choice parameter."""
    if options is None or not options.tools:
        return
    payload["tools"] = [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters or {},
            },
        }
        for tool in options.tools
    ]


def _parse_tool_calls(raw: list | None, start_index: int = 0) -> list[ToolCall]:
    """Parse Ollama wire tool calls, synthesizing ids 'call_N' in order.

    Ollama returns arguments as parsed objects already.
    """
    calls: list[ToolCall] = []
    for item in raw or []:
        function = item.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        arguments = function.get("arguments")
        calls.append(ToolCall(
            id=f"call_{start_index + len(calls)}",
            name=name,
            arguments=arguments if isinstance(arguments, dict) else {},
        ))
    return calls
