from __future__ import annotations

import asyncio
import json
import threading
from typing import AsyncGenerator

import httpx

from priest.errors import ProviderError, ProviderTimeoutError
from priest.providers.base import AdapterResult, ProviderAdapter
from priest.schema.request import OutputSpec, PriestConfig

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
    ) -> AdapterResult:
        # Anthropic requires system content as a top-level field.
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        turns = [m for m in messages if m["role"] != "system"]

        payload: dict = {
            "model": config.model,
            "messages": turns,
            "max_tokens": config.max_output_tokens or _DEFAULT_MAX_TOKENS,
        }

        if system_parts:
            payload["system"] = "\n\n".join(system_parts)

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

        usage = data.get("usage", {})
        return AdapterResult(
            text=text,
            raw=data,
            finish_reason=_map_finish_reason(data.get("stop_reason")),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )


    async def stream(
        self,
        messages: list[dict],
        config: PriestConfig,
        output_spec: OutputSpec,
    ) -> AsyncGenerator[str, None]:
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        turns = [m for m in messages if m["role"] != "system"]

        payload: dict = {
            "model": config.model,
            "messages": turns,
            "max_tokens": config.max_output_tokens or _DEFAULT_MAX_TOKENS,
            "stream": True,
        }

        if system_parts:
            payload["system"] = "\n\n".join(system_parts)

        payload.update(config.provider_options)

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        timeout = config.timeout_seconds or 60.0
        loop = asyncio.get_event_loop()
        q: asyncio.Queue[str | Exception | None] = asyncio.Queue()

        def _run() -> None:
            try:
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
                            if data.get("type") == "content_block_delta":
                                text = data.get("delta", {}).get("text", "")
                                if text:
                                    loop.call_soon_threadsafe(q.put_nowait, text)
            except httpx.TimeoutException as exc:
                loop.call_soon_threadsafe(q.put_nowait, exc)
            except httpx.HTTPStatusError as exc:
                loop.call_soon_threadsafe(q.put_nowait, exc)
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


def _map_finish_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    return {"end_turn": "stop", "max_tokens": "length", "stop_sequence": "stop"}.get(reason, "unknown")
