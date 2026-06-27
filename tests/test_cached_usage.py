"""Cached input-token visibility (spec 2.5.0): prompt-cache hits surfaced on
AdapterResult.cached_input_tokens and the streaming usage event."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from priest.providers.anthropic_provider import AnthropicProvider
from priest.providers.openai_compat_provider import OpenAICompatProvider
from priest.schema.request import OutputSpec, PriestConfig

_CONFIG = PriestConfig(provider="test", model="test-model")
_MESSAGES = [{"role": "user", "content": "hi"}]


# ---------------------------------------------------------------------------
# OpenAI-compatible
# ---------------------------------------------------------------------------

def _openai_response(cached: int | None):
    details = SimpleNamespace(cached_tokens=cached) if cached is not None else None
    usage = SimpleNamespace(prompt_tokens=1200, completion_tokens=40, prompt_tokens_details=details)
    choice = SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None), finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=usage, model_dump=lambda: {})


@pytest.mark.asyncio
async def test_openai_complete_parses_cached_tokens():
    adapter = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk-test")

    def _fake_call(*, api_key, base_url, timeout, proxy, kwargs):
        return _openai_response(1024)

    with patch("priest.providers.openai_compat_provider._call_sync", side_effect=_fake_call):
        with patch("anyio.to_thread.run_sync", new=AsyncMock(side_effect=lambda fn: fn())):
            result = await adapter.complete(_MESSAGES, _CONFIG, OutputSpec())

    assert result.input_tokens == 1200
    assert result.cached_input_tokens == 1024


@pytest.mark.asyncio
async def test_openai_complete_cached_tokens_none_when_omitted():
    adapter = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk-test")

    def _fake_call(*, api_key, base_url, timeout, proxy, kwargs):
        return _openai_response(None)

    with patch("priest.providers.openai_compat_provider._call_sync", side_effect=_fake_call):
        with patch("anyio.to_thread.run_sync", new=AsyncMock(side_effect=lambda fn: fn())):
            result = await adapter.complete(_MESSAGES, _CONFIG, OutputSpec())

    assert result.cached_input_tokens is None


@pytest.mark.asyncio
async def test_openai_stream_emits_cached_tokens_on_usage_event():
    adapter = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk-test")

    chunk_text = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="hi", tool_calls=None), finish_reason=None)],
        usage=None,
    )
    chunk_final = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=None), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=40, prompt_tokens_details=SimpleNamespace(cached_tokens=1024)),
    )

    def _fake_client(**_):
        client = MagicMock()
        client.chat.completions.create.side_effect = lambda **__: iter([chunk_text, chunk_final])
        return client

    events = []
    with patch("priest.providers.openai_compat_provider.OpenAI", side_effect=_fake_client):
        async for e in adapter.stream_events(_MESSAGES, _CONFIG, OutputSpec()):
            events.append(e)

    usage = next(e for e in events if e.type == "usage")
    assert usage.input_tokens == 1200
    assert usage.output_tokens == 40
    assert usage.cached_input_tokens == 1024


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_anthropic_complete_parses_cache_read_input_tokens():
    adapter = AnthropicProvider(api_key="sk-test")

    class FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 1200, "output_tokens": 40, "cache_read_input_tokens": 1024},
                "stop_reason": "end_turn",
            }

    async def _fake_post(url, *, json=None, headers=None, timeout=None):
        return FakeResp()

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.post = AsyncMock(side_effect=_fake_post)

    with patch("httpx.AsyncClient", return_value=fake_client):
        result = await adapter.complete(_MESSAGES, _CONFIG, OutputSpec())

    assert result.input_tokens == 1200
    assert result.cached_input_tokens == 1024


@pytest.mark.asyncio
async def test_anthropic_stream_emits_cached_tokens_from_message_start():
    adapter = AnthropicProvider(api_key="sk-test")

    sse_lines = [
        'data: {"type": "message_start", "message": {"usage": {"input_tokens": 1200, "cache_read_input_tokens": 1024}}}',
        'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}',
        'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 40}}',
        'data: {"type": "message_stop"}',
    ]

    class FakeSyncResponse:
        def raise_for_status(self): pass
        def iter_lines(self): return iter(sse_lines)
        def __enter__(self): return self
        def __exit__(self, *_): pass

    class FakeSyncStream:
        def __init__(self, method, url, *, json=None, headers=None, timeout=None): pass
        def __enter__(self): return FakeSyncResponse()
        def __exit__(self, *_): pass

    fake_sync_client = MagicMock()
    fake_sync_client.stream = FakeSyncStream
    fake_sync_client.__enter__ = MagicMock(return_value=fake_sync_client)
    fake_sync_client.__exit__ = MagicMock(return_value=None)

    events = []
    with patch("httpx.Client", return_value=fake_sync_client):
        async for e in adapter.stream_events(_MESSAGES, _CONFIG, OutputSpec()):
            events.append(e)

    usage = next(e for e in events if e.type == "usage")
    assert usage.input_tokens == 1200
    assert usage.output_tokens == 40
    assert usage.cached_input_tokens == 1024
