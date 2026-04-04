from __future__ import annotations

import json
from typing import AsyncGenerator

import httpx

from priest.errors import ProviderError, ProviderTimeoutError
from priest.providers.base import AdapterResult, ProviderAdapter
from priest.schema.request import OutputSpec, PriestConfig

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
    ) -> AdapterResult:
        payload: dict = {
            "model": config.model,
            "messages": messages,
            "stream": False,
        }

        if config.max_output_tokens is not None:
            payload.setdefault("options", {})["num_predict"] = config.max_output_tokens

        if output_spec.provider_format == "json":
            payload["format"] = "json"

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

        # Ollama returns token counts in the top-level response
        return AdapterResult(
            text=text,
            raw=data,
            finish_reason=_map_finish_reason(data.get("done_reason")),
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
        )

    async def stream(
        self,
        messages: list[dict],
        config: PriestConfig,
        output_spec: OutputSpec,
    ) -> AsyncGenerator[str, None]:
        payload: dict = {
            "model": config.model,
            "messages": messages,
            "stream": True,
        }

        if config.max_output_tokens is not None:
            payload.setdefault("options", {})["num_predict"] = config.max_output_tokens

        if output_spec.provider_format == "json":
            payload["format"] = "json"

        payload.update(config.provider_options)

        timeout = config.timeout_seconds or 60.0

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
                        data = json.loads(line)
                        content = data.get("message", {}).get("content", "")
                        if content:
                            yield content
                        if data.get("done"):
                            break
        except httpx.TimeoutException:
            raise ProviderTimeoutError("ollama", timeout)
        except httpx.HTTPStatusError as exc:
            raise ProviderError("ollama", f"HTTP {exc.response.status_code}: {exc.response.text}")
        except httpx.RequestError as exc:
            raise ProviderError("ollama", str(exc))


def _map_finish_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    mapping = {
        "stop": "stop",
        "length": "length",
        "load": "stop",
    }
    return mapping.get(reason, "unknown")
