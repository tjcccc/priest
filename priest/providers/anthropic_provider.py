from __future__ import annotations

import asyncio
import json
import threading
from typing import AsyncGenerator

import httpx

from priest.errors import ProviderError, ProviderTimeoutError
from priest.providers.base import AdapterCallOptions, AdapterResult, AdapterStreamEvent, ProviderAdapter
from priest.schema.request import NamedToolChoice, OutputSpec, PriestConfig, ToolCall

_DEFAULT_BASE_URL = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 8096


class AnthropicProvider(ProviderAdapter):
    """Calls the Anthropic /v1/messages endpoint.

    Anthropic's API shape differs from OpenAI: system content is a top-level
    field, not a message in the array, and auth uses x-api-key header.
    """

    provider_name = "anthropic"

    def __init__(self, api_key: str, base_url: str = _DEFAULT_BASE_URL, proxy: str | None = None) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._proxy = proxy

    async def complete(
        self,
        messages: list[dict],
        config: PriestConfig,
        output_spec: OutputSpec,
        options: AdapterCallOptions | None = None,
    ) -> AdapterResult:
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        if output_spec.json_schema is not None:
            system_parts.append(_schema_instruction(output_spec.json_schema))
        turns = _translate_messages([m for m in messages if m["role"] != "system"])

        payload: dict = {
            "model": config.model,
            "messages": turns,
            "max_tokens": config.max_output_tokens or _DEFAULT_MAX_TOKENS,
        }

        if system_parts:
            payload["system"] = "\n\n".join(system_parts)

        _apply_tools(payload, options)
        payload.update(config.provider_options)

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        timeout = config.timeout_seconds or 60.0

        try:
            async with httpx.AsyncClient(proxy=self._proxy) as client:
                response = await client.post(
                    f"{self._base_url}/v1/messages",
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            raise ProviderTimeoutError("anthropic", timeout)
        except httpx.HTTPStatusError as exc:
            raise ProviderError("anthropic", f"HTTP {exc.response.status_code}: {exc.response.text}")
        except httpx.RequestError as exc:
            raise ProviderError("anthropic", str(exc))

        data = response.json()
        content = data.get("content", [])
        text = next((c.get("text") for c in content if c.get("type") == "text"), None)
        tool_calls = _parse_tool_use_blocks(content)

        usage = data.get("usage", {})
        return AdapterResult(
            text=text,
            raw=data,
            finish_reason="tool_calls" if tool_calls else _map_finish_reason(data.get("stop_reason")),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
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
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        if output_spec.json_schema is not None:
            system_parts.append(_schema_instruction(output_spec.json_schema))
        turns = _translate_messages([m for m in messages if m["role"] != "system"])

        payload: dict = {
            "model": config.model,
            "messages": turns,
            "max_tokens": config.max_output_tokens or _DEFAULT_MAX_TOKENS,
            "stream": True,
        }

        if system_parts:
            payload["system"] = "\n\n".join(system_parts)

        _apply_tools(payload, options)
        payload.update(config.provider_options)

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        timeout = config.timeout_seconds or 60.0
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[AdapterStreamEvent | Exception | None] = asyncio.Queue()

        def _emit(event: AdapterStreamEvent) -> None:
            loop.call_soon_threadsafe(q.put_nowait, event)

        def _run() -> None:
            try:
                # Anthropic block index -> in-progress tool call state.
                # Tool-call event indexes are assigned in tool_use block order.
                tool_blocks: dict[int, dict] = {}
                tool_count = 0
                stop_reason: str | None = None
                input_tokens: int | None = None
                output_tokens: int | None = None
                with httpx.Client(proxy=self._proxy) as client:
                    with client.stream(
                        "POST",
                        f"{self._base_url}/v1/messages",
                        json=payload,
                        headers=headers,
                        timeout=timeout,
                    ) as response:
                        response.raise_for_status()
                        for line in response.iter_lines():
                            if not line.startswith("data: "):
                                continue
                            raw = line[6:]
                            if raw == "[DONE]":
                                break
                            try:
                                data = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            event_type = data.get("type")
                            if event_type == "message_start":
                                usage = data.get("message", {}).get("usage", {})
                                input_tokens = usage.get("input_tokens", input_tokens)
                            elif event_type == "content_block_start":
                                block = data.get("content_block", {})
                                index = data.get("index")
                                if block.get("type") == "tool_use" and index is not None:
                                    tool_index = tool_count
                                    tool_count += 1
                                    tool_blocks[index] = {
                                        "tool_index": tool_index,
                                        "id": block.get("id"),
                                        "name": block.get("name"),
                                        "json": "",
                                    }
                                    _emit(AdapterStreamEvent(
                                        type="tool_call_start", index=tool_index,
                                        id=block.get("id"), name=block.get("name"),
                                    ))
                            elif event_type == "content_block_delta":
                                delta = data.get("delta", {})
                                if delta.get("type") == "text_delta" and delta.get("text"):
                                    _emit(AdapterStreamEvent(type="text_delta", text=delta["text"]))
                                elif delta.get("type") == "input_json_delta":
                                    state = tool_blocks.get(data.get("index"))
                                    fragment = delta.get("partial_json", "")
                                    if state is not None and fragment:
                                        state["json"] += fragment
                                        _emit(AdapterStreamEvent(
                                            type="tool_call_delta",
                                            index=state["tool_index"],
                                            arguments_delta=fragment,
                                        ))
                            elif event_type == "content_block_stop":
                                state = tool_blocks.pop(data.get("index"), None)
                                if state is not None:
                                    _emit(AdapterStreamEvent(
                                        type="tool_call_end",
                                        index=state["tool_index"],
                                        tool_call=ToolCall(
                                            id=state["id"] or f"call_{state['tool_index']}",
                                            name=state["name"] or "",
                                            arguments=_parse_arguments(state["json"]),
                                        ),
                                    ))
                            elif event_type == "message_delta":
                                stop_reason = data.get("delta", {}).get("stop_reason", stop_reason)
                                usage = data.get("usage", {})
                                output_tokens = usage.get("output_tokens", output_tokens)
                if input_tokens is not None or output_tokens is not None:
                    _emit(AdapterStreamEvent(
                        type="usage", input_tokens=input_tokens, output_tokens=output_tokens,
                    ))
                _emit(AdapterStreamEvent(
                    type="finish",
                    finish_reason="tool_calls" if tool_count > 0 else _map_finish_reason(stop_reason),
                ))
            except Exception as exc:
                loop.call_soon_threadsafe(q.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                if isinstance(item, httpx.TimeoutException):
                    raise ProviderTimeoutError("anthropic", timeout)
                if isinstance(item, httpx.HTTPStatusError):
                    raise ProviderError("anthropic", f"HTTP {item.response.status_code}: {item.response.text}")
                if isinstance(item, Exception):
                    raise ProviderError("anthropic", str(item))
                yield item
        finally:
            thread.join(timeout=5)


def _schema_instruction(schema: dict) -> str:
    return (
        "Respond with a valid JSON object that conforms to the following JSON Schema:\n\n"
        f"<schema>\n{json.dumps(schema, indent=2)}\n</schema>\n\n"
        "Return only the JSON object — no explanation, no markdown fences."
    )


def _translate_messages(messages: list[dict]) -> list[dict]:
    """Translate OpenAI-format multimodal content blocks to Anthropic's format.

    OpenAI image_url blocks become Anthropic image blocks.
    Data URIs are split into base64 + media_type. HTTP/HTTPS URLs use Anthropic's url source.
    Text-only messages (string content) are passed through unchanged.
    """
    result = []
    pending_tool_results: list[dict] = []

    def flush_tool_results() -> None:
        if pending_tool_results:
            result.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for msg in messages:
        content = msg.get("content")
        if msg.get("role") == "tool":
            # Consecutive tool results merge into one user message
            # (Anthropic requires alternating roles).
            pending_tool_results.append({
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id"),
                "content": content or "",
            })
            continue
        flush_tool_results()
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            blocks = []
            if content:
                blocks.append({"type": "text", "text": content})
            for call in msg["tool_calls"]:
                blocks.append({
                    "type": "tool_use",
                    "id": call["id"],
                    "name": call["name"],
                    "input": call.get("arguments", {}),
                })
            result.append({"role": "assistant", "content": blocks})
            continue
        if isinstance(content, list):
            blocks: list[dict] = []
            for block in content:
                if block.get("type") == "text":
                    blocks.append({"type": "text", "text": block["text"]})
                elif block.get("type") == "image_url":
                    url: str = block["image_url"]["url"]
                    if url.startswith("data:"):
                        header, b64data = url.split(",", 1)
                        media_type = header.split(":")[1].split(";")[0]
                        blocks.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": b64data},
                        })
                    else:
                        blocks.append({
                            "type": "image",
                            "source": {"type": "url", "url": url},
                        })
            result.append({"role": msg["role"], "content": blocks})
        else:
            result.append(msg)
    flush_tool_results()
    return result


def _map_finish_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    return {"end_turn": "stop", "max_tokens": "length", "stop_sequence": "stop"}.get(reason, "unknown")


def _apply_tools(payload: dict, options: AdapterCallOptions | None) -> None:
    if options is None or not options.tools:
        return
    payload["tools"] = [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters or {"type": "object", "properties": {}},
        }
        for tool in options.tools
    ]
    if options.tool_choice is not None:
        if isinstance(options.tool_choice, NamedToolChoice):
            payload["tool_choice"] = {"type": "tool", "name": options.tool_choice.name}
        elif options.tool_choice == "required":
            payload["tool_choice"] = {"type": "any"}
        else:
            payload["tool_choice"] = {"type": options.tool_choice}


def _parse_tool_use_blocks(content: list) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for i, block in enumerate(content):
        if block.get("type") != "tool_use" or not block.get("name"):
            continue
        arguments = block.get("input")
        calls.append(ToolCall(
            id=block.get("id") or f"call_{i}",
            name=block["name"],
            arguments=arguments if isinstance(arguments, dict) else {},
        ))
    return calls


def _parse_arguments(raw: str) -> dict:
    """Per spec, unparseable or non-object argument JSON becomes {}."""
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
