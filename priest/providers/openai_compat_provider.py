from __future__ import annotations

import asyncio
import json
import threading
from functools import partial
from typing import AsyncGenerator

import anyio
from openai import OpenAI, APIConnectionError, APIStatusError, APITimeoutError

from priest.errors import ProviderError, ProviderTimeoutError
from priest.providers.base import AdapterCallOptions, AdapterResult, AdapterStreamEvent, ProviderAdapter
from priest.schema.request import NamedToolChoice, OutputSpec, PriestConfig, ToolCall


class OpenAICompatProvider(ProviderAdapter):
    """Adapter for any OpenAI-compatible /v1/chat/completions endpoint.

    Covers: OpenAI, Gemini, Bailian, Alibaba Cloud, MiniMax, Groq,
    OpenRouter, and any custom base_url.

    Uses the synchronous OpenAI client in a thread to avoid Python 3.14+
    incompatibilities with httpcore's anyio async TLS backend.
    """

    def __init__(self, name: str, base_url: str, api_key: str = "", proxy: str | None = None) -> None:
        self._name = name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._proxy = proxy

    @property
    def provider_name(self) -> str:
        return self._name

    async def complete(
        self,
        messages: list[dict],
        config: PriestConfig,
        output_spec: OutputSpec,
        options: AdapterCallOptions | None = None,
    ) -> AdapterResult:
        kwargs: dict = {
            "model": config.model,
            "messages": _translate_messages(messages),
        }
        _apply_tools(kwargs, options)

        if config.max_output_tokens is not None:
            kwargs["max_tokens"] = config.max_output_tokens

        if output_spec.json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": output_spec.json_schema_name,
                    "schema": output_spec.json_schema,
                    "strict": output_spec.json_schema_strict,
                },
            }
        elif output_spec.provider_format == "json":
            kwargs["response_format"] = {"type": "json_object"}

        if config.provider_options:
            kwargs["extra_body"] = config.provider_options

        call = partial(
            _call_sync,
            api_key=self._api_key or "dummy",
            base_url=self._base_url,
            timeout=config.timeout_seconds or 60.0,
            proxy=self._proxy,
            kwargs=kwargs,
        )
        try:
            response = await anyio.to_thread.run_sync(call)
        except APITimeoutError:
            raise ProviderTimeoutError(self._name, config.timeout_seconds or 60.0)
        except APIStatusError as exc:
            raise ProviderError(self._name, f"HTTP {exc.status_code}: {exc.message}")
        except APIConnectionError as exc:
            raise ProviderError(self._name, str(exc))

        choices = response.choices
        message = choices[0].message if choices else None
        text = message.content if message else None
        tool_calls = _parse_tool_calls(message.tool_calls if message else None)
        finish_reason = (
            "tool_calls" if tool_calls
            else _map_finish_reason(choices[0].finish_reason if choices else None)
        )

        usage = response.usage
        return AdapterResult(
            text=text,
            raw=response.model_dump(),
            finish_reason=finish_reason,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            cached_input_tokens=_cached_tokens(usage),
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
        kwargs: dict = {
            "model": config.model,
            "messages": _translate_messages(messages),
            "stream": True,
        }
        _apply_tools(kwargs, options)

        if config.max_output_tokens is not None:
            kwargs["max_tokens"] = config.max_output_tokens

        if output_spec.json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": output_spec.json_schema_name,
                    "schema": output_spec.json_schema,
                    "strict": output_spec.json_schema_strict,
                },
            }
        elif output_spec.provider_format == "json":
            kwargs["response_format"] = {"type": "json_object"}

        # Streaming usage is opt-in: without stream_options.include_usage,
        # OpenAI-compatible gateways (e.g. DashScope) emit a usage chunk only for
        # models that volunteer it, so cost/context goes missing for the rest. Sent
        # via extra_body so config.provider_options can still override (or drop) it.
        extra_body: dict = {"stream_options": {"include_usage": True}}
        if config.provider_options:
            extra_body = {**extra_body, **config.provider_options}
        kwargs["extra_body"] = extra_body

        loop = asyncio.get_running_loop()
        q: asyncio.Queue[AdapterStreamEvent | Exception | None] = asyncio.Queue()

        def _emit(event: AdapterStreamEvent) -> None:
            loop.call_soon_threadsafe(q.put_nowait, event)

        def _run() -> None:
            try:
                import httpx as _httpx
                http_client = _httpx.Client(proxy=self._proxy) if self._proxy else None
                client = OpenAI(
                    api_key=self._api_key or "dummy",
                    base_url=self._base_url,
                    timeout=config.timeout_seconds or 60.0,
                    max_retries=0,
                    http_client=http_client,
                )
                response = client.chat.completions.create(**kwargs)
                # Tool-call fragments accumulate per index until the stream ends.
                partials: dict[int, dict] = {}
                finish_reason: str | None = None
                usage = None
                for chunk in response:
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage is not None:
                        usage = chunk_usage
                    choices = chunk.choices
                    if not choices:
                        continue
                    choice = choices[0]
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                    if choice.delta.content:
                        _emit(AdapterStreamEvent(type="text_delta", text=choice.delta.content))
                    for fragment in choice.delta.tool_calls or []:
                        index = fragment.index or 0
                        partial = partials.get(index)
                        if partial is None:
                            partial = {
                                "id": fragment.id,
                                "name": fragment.function.name if fragment.function else None,
                                "args": "",
                            }
                            partials[index] = partial
                            _emit(AdapterStreamEvent(
                                type="tool_call_start", index=index,
                                id=partial["id"], name=partial["name"],
                            ))
                        else:
                            if fragment.id:
                                partial["id"] = fragment.id
                            if fragment.function and fragment.function.name:
                                partial["name"] = fragment.function.name
                        args_delta = fragment.function.arguments if fragment.function else None
                        if args_delta:
                            partial["args"] += args_delta
                            _emit(AdapterStreamEvent(
                                type="tool_call_delta", index=index, arguments_delta=args_delta,
                            ))
                for index in sorted(partials):
                    partial = partials[index]
                    _emit(AdapterStreamEvent(
                        type="tool_call_end",
                        index=index,
                        tool_call=ToolCall(
                            id=partial["id"] or f"call_{index}",
                            name=partial["name"] or "",
                            arguments=_parse_arguments(partial["args"]),
                        ),
                    ))
                if usage is not None:
                    _emit(AdapterStreamEvent(
                        type="usage",
                        input_tokens=getattr(usage, "prompt_tokens", None),
                        output_tokens=getattr(usage, "completion_tokens", None),
                        cached_input_tokens=_cached_tokens(usage),
                    ))
                _emit(AdapterStreamEvent(
                    type="finish",
                    finish_reason="tool_calls" if partials else _map_finish_reason(finish_reason) or "stop",
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
                if isinstance(item, APITimeoutError):
                    raise ProviderTimeoutError(self._name, config.timeout_seconds or 60.0)
                if isinstance(item, APIStatusError):
                    raise ProviderError(self._name, f"HTTP {item.status_code}: {item.message}")
                if isinstance(item, APIConnectionError):
                    raise ProviderError(self._name, str(item))
                if isinstance(item, Exception):
                    raise ProviderError(self._name, str(item))
                yield item
        finally:
            thread.join(timeout=5)


def _call_sync(*, api_key: str, base_url: str, timeout: float, proxy: str | None, kwargs: dict):
    """Sync call executed in a worker thread.

    Uses the synchronous OpenAI client so httpcore uses its plain socket
    backend instead of the anyio async TLS backend (broken on Python 3.14).
    """
    import httpx

    http_client = httpx.Client(proxy=proxy) if proxy else None
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=0,
        http_client=http_client,
    )
    return client.chat.completions.create(**kwargs)


def _cached_tokens(usage: object | None) -> int | None:
    """Prompt-cache hit count from usage.prompt_tokens_details.cached_tokens (spec 2.5.0).

    None when usage or the nested detail is absent (most models/providers omit it).
    """
    if usage is None:
        return None
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return None
    return getattr(details, "cached_tokens", None)


def _map_finish_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    return {"stop": "stop", "length": "length", "content_filter": "content_filter"}.get(reason, "unknown")


def _translate_messages(messages: list[dict]) -> list[dict]:
    """Translate the neutral tool format to OpenAI wire format.

    Assistant tool calls serialize arguments to JSON strings; tool turns
    carry tool_call_id. Other messages pass through unchanged.
    """
    result = []
    for msg in messages:
        if msg.get("role") == "tool":
            result.append({
                "role": "tool",
                "tool_call_id": msg.get("tool_call_id"),
                "content": msg.get("content", ""),
            })
        elif msg.get("role") == "assistant" and msg.get("tool_calls"):
            result.append({
                "role": "assistant",
                "content": msg.get("content") or None,
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call.get("arguments", {})),
                        },
                    }
                    for call in msg["tool_calls"]
                ],
            })
        else:
            result.append(msg)
    return result


def _apply_tools(kwargs: dict, options: AdapterCallOptions | None) -> None:
    if options is None or not options.tools:
        return
    kwargs["tools"] = [
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
    if options.tool_choice is not None:
        if isinstance(options.tool_choice, NamedToolChoice):
            kwargs["tool_choice"] = {"type": "function", "function": {"name": options.tool_choice.name}}
        else:
            kwargs["tool_choice"] = options.tool_choice


def _parse_tool_calls(raw: list | None) -> list[ToolCall]:
    """Parse non-streaming wire tool calls. Unparseable argument JSON becomes {}."""
    calls: list[ToolCall] = []
    for i, item in enumerate(raw or []):
        function = getattr(item, "function", None)
        name = getattr(function, "name", None) if function else None
        if not name:
            continue
        calls.append(ToolCall(
            id=getattr(item, "id", None) or f"call_{i}",
            name=name,
            arguments=_parse_arguments(getattr(function, "arguments", "") or ""),
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
