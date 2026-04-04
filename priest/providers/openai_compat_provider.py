from __future__ import annotations

from functools import partial

import anyio
from openai import OpenAI, APIConnectionError, APIStatusError, APITimeoutError

from priest.errors import ProviderError, ProviderTimeoutError
from priest.providers.base import AdapterResult, ProviderAdapter
from priest.schema.request import OutputSpec, PriestConfig


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
    ) -> AdapterResult:
        kwargs: dict = {
            "model": config.model,
            "messages": messages,
        }

        if config.max_output_tokens is not None:
            kwargs["max_tokens"] = config.max_output_tokens

        if output_spec.provider_format == "json":
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
        text = choices[0].message.content if choices else None
        finish_reason = _map_finish_reason(choices[0].finish_reason if choices else None)

        usage = response.usage
        return AdapterResult(
            text=text,
            raw=response.model_dump(),
            finish_reason=finish_reason,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )


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


def _map_finish_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    return {"stop": "stop", "length": "length", "content_filter": "content_filter"}.get(reason, "unknown")
