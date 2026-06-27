"""Tests for json_schema structured output wiring across provider adapters."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from priest.providers.anthropic_provider import AnthropicProvider, _schema_instruction
from priest.providers.ollama_provider import OllamaProvider
from priest.providers.openai_compat_provider import OpenAICompatProvider
from priest.schema.request import OutputSpec, PriestConfig

_SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
_CONFIG = PriestConfig(provider="test", model="test-model")
_MESSAGES = [{"role": "user", "content": "hello"}]


# ---------------------------------------------------------------------------
# OutputSpec defaults
# ---------------------------------------------------------------------------

def test_output_spec_defaults():
    spec = OutputSpec()
    assert spec.json_schema is None
    assert spec.json_schema_name == "response"
    assert spec.json_schema_strict is False


def test_output_spec_json_schema_round_trip():
    spec = OutputSpec(json_schema=_SCHEMA, json_schema_name="my_obj", json_schema_strict=True)
    assert spec.json_schema == _SCHEMA
    assert spec.json_schema_name == "my_obj"
    assert spec.json_schema_strict is True


# ---------------------------------------------------------------------------
# OpenAI-compat adapter
# ---------------------------------------------------------------------------

def _make_openai_response(text: str = "{}"):
    choice = MagicMock()
    choice.message.content = text
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    resp.model_dump.return_value = {}
    resp.usage.prompt_tokens = 5
    resp.usage.completion_tokens = 3
    return resp


@pytest.mark.asyncio
async def test_openai_json_schema_complete():
    adapter = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk-test")
    spec = OutputSpec(json_schema=_SCHEMA, json_schema_name="result")

    captured: dict = {}

    def _fake_call(*, api_key, base_url, timeout, proxy, kwargs):
        captured.update(kwargs)
        return _make_openai_response()

    with patch("priest.providers.openai_compat_provider._call_sync", side_effect=_fake_call):
        with patch("anyio.to_thread.run_sync", new=AsyncMock(side_effect=lambda fn: fn())):
            await adapter.complete(_MESSAGES, _CONFIG, spec)

    rf = captured.get("response_format", {})
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "result"
    assert rf["json_schema"]["schema"] == _SCHEMA
    assert rf["json_schema"]["strict"] is False


@pytest.mark.asyncio
async def test_openai_provider_format_json_not_overridden_by_schema():
    """json_schema takes precedence over provider_format='json'."""
    adapter = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk-test")
    spec = OutputSpec(json_schema=_SCHEMA, provider_format="json")

    captured: dict = {}

    def _fake_call(*, api_key, base_url, timeout, proxy, kwargs):
        captured.update(kwargs)
        return _make_openai_response()

    with patch("priest.providers.openai_compat_provider._call_sync", side_effect=_fake_call):
        with patch("anyio.to_thread.run_sync", new=AsyncMock(side_effect=lambda fn: fn())):
            await adapter.complete(_MESSAGES, _CONFIG, spec)

    assert captured["response_format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_openai_provider_format_json_without_schema():
    """provider_format='json' still works when json_schema is None."""
    adapter = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk-test")
    spec = OutputSpec(provider_format="json")

    captured: dict = {}

    def _fake_call(*, api_key, base_url, timeout, proxy, kwargs):
        captured.update(kwargs)
        return _make_openai_response()

    with patch("priest.providers.openai_compat_provider._call_sync", side_effect=_fake_call):
        with patch("anyio.to_thread.run_sync", new=AsyncMock(side_effect=lambda fn: fn())):
            await adapter.complete(_MESSAGES, _CONFIG, spec)

    assert captured["response_format"] == {"type": "json_object"}


# ---------------------------------------------------------------------------
# OpenAI-compat streaming usage opt-in (stream_options.include_usage)
# ---------------------------------------------------------------------------

def _fake_streaming_client(captured: dict):
    """Stand-in for OpenAI() that records create() kwargs and yields no chunks."""
    client = MagicMock()

    def _create(**kwargs):
        captured.update(kwargs)
        return iter(())  # no chunks → stream ends cleanly, finish is emitted

    client.chat.completions.create.side_effect = _create
    return client


@pytest.mark.asyncio
async def test_openai_streaming_requests_usage():
    """Streaming requests ask for usage via extra_body.stream_options.include_usage."""
    adapter = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk-test")
    captured: dict = {}

    with patch(
        "priest.providers.openai_compat_provider.OpenAI",
        side_effect=lambda **_: _fake_streaming_client(captured),
    ):
        async for _ in adapter.stream_events(_MESSAGES, _CONFIG, OutputSpec()):
            pass

    assert captured["stream"] is True
    assert captured["extra_body"]["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_openai_complete_omits_stream_options():
    """Non-streaming complete() is unchanged: no stream, no stream_options."""
    adapter = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk-test")
    captured: dict = {}

    def _fake_call(*, api_key, base_url, timeout, proxy, kwargs):
        captured.update(kwargs)
        return _make_openai_response()

    with patch("priest.providers.openai_compat_provider._call_sync", side_effect=_fake_call):
        with patch("anyio.to_thread.run_sync", new=AsyncMock(side_effect=lambda fn: fn())):
            await adapter.complete(_MESSAGES, _CONFIG, OutputSpec())

    assert captured.get("stream") is None
    assert "stream_options" not in captured
    assert "stream_options" not in captured.get("extra_body", {})


@pytest.mark.asyncio
async def test_openai_streaming_usage_override_via_provider_options():
    """provider_options can override (or drop) the include_usage default."""
    adapter = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk-test")
    config = PriestConfig(
        provider="test",
        model="test-model",
        provider_options={"stream_options": {"include_usage": False}},
    )
    captured: dict = {}

    with patch(
        "priest.providers.openai_compat_provider.OpenAI",
        side_effect=lambda **_: _fake_streaming_client(captured),
    ):
        async for _ in adapter.stream_events(_MESSAGES, config, OutputSpec()):
            pass

    assert captured["extra_body"]["stream_options"] == {"include_usage": False}


# ---------------------------------------------------------------------------
# Ollama adapter
# ---------------------------------------------------------------------------

def _make_ollama_response(text: str = "{}") -> dict:
    return {"message": {"content": text}, "done": True, "done_reason": "stop"}


@pytest.mark.asyncio
async def test_ollama_json_schema_complete():
    adapter = OllamaProvider()
    spec = OutputSpec(json_schema=_SCHEMA)

    captured: dict = {}

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return _make_ollama_response()

    async def _fake_post(url, *, json=None, timeout=None):
        captured.update(json or {})
        return FakeResp()

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.post = AsyncMock(side_effect=_fake_post)

    with patch("httpx.AsyncClient", return_value=fake_client):
        await adapter.complete(_MESSAGES, _CONFIG, spec)

    assert captured.get("format") == _SCHEMA


@pytest.mark.asyncio
async def test_ollama_provider_format_json_without_schema():
    adapter = OllamaProvider()
    spec = OutputSpec(provider_format="json")

    captured: dict = {}

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return _make_ollama_response()

    async def _fake_post(url, *, json=None, timeout=None):
        captured.update(json or {})
        return FakeResp()

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.post = AsyncMock(side_effect=_fake_post)

    with patch("httpx.AsyncClient", return_value=fake_client):
        await adapter.complete(_MESSAGES, _CONFIG, spec)

    assert captured.get("format") == "json"


# ---------------------------------------------------------------------------
# Anthropic adapter
# ---------------------------------------------------------------------------

def test_schema_instruction_contains_schema():
    instruction = _schema_instruction(_SCHEMA)
    assert "<schema>" in instruction
    assert json.dumps(_SCHEMA, indent=2) in instruction
    assert "JSON Schema" in instruction


@pytest.mark.asyncio
async def test_anthropic_json_schema_complete():
    adapter = AnthropicProvider(api_key="sk-test")
    spec = OutputSpec(json_schema=_SCHEMA)

    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Give me a name."},
    ]

    captured: dict = {}

    class FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"content": [{"type": "text", "text": "{}"}], "usage": {}, "stop_reason": "end_turn"}

    async def _fake_post(url, *, json=None, headers=None, timeout=None):
        captured.update(json or {})
        return FakeResp()

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.post = AsyncMock(side_effect=_fake_post)

    with patch("httpx.AsyncClient", return_value=fake_client):
        await adapter.complete(messages, _CONFIG, spec)

    system = captured.get("system", "")
    assert "You are helpful." in system
    assert "<schema>" in system
    assert json.dumps(_SCHEMA, indent=2) in system


@pytest.mark.asyncio
async def test_anthropic_no_schema_no_injection():
    """When json_schema is None, system prompt is not modified."""
    adapter = AnthropicProvider(api_key="sk-test")
    spec = OutputSpec()

    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hi"},
    ]

    captured: dict = {}

    class FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"content": [{"type": "text", "text": "hi"}], "usage": {}, "stop_reason": "end_turn"}

    async def _fake_post(url, *, json=None, headers=None, timeout=None):
        captured.update(json or {})
        return FakeResp()

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.post = AsyncMock(side_effect=_fake_post)

    with patch("httpx.AsyncClient", return_value=fake_client):
        await adapter.complete(messages, _CONFIG, spec)

    assert captured.get("system") == "You are helpful."


# ---------------------------------------------------------------------------
# Streaming paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_openai_json_schema_stream():
    adapter = OpenAICompatProvider("openai", "https://api.openai.com/v1", "sk-test")
    spec = OutputSpec(json_schema=_SCHEMA, json_schema_name="result")

    captured_kwargs: dict = {}

    class FakeDelta:
        content = "{"
        tool_calls = None

    class FakeChoice:
        delta = FakeDelta()
        finish_reason = None

    class FakeChunk:
        choices = [FakeChoice()]
        usage = None

    def make_openai_client(**_client_kwargs):
        client = MagicMock()
        def create(**kwargs):
            captured_kwargs.update(kwargs)
            return iter([FakeChunk()])
        client.chat.completions.create = create
        return client

    with patch("priest.providers.openai_compat_provider.OpenAI", side_effect=make_openai_client):
        chunks = [c async for c in adapter.stream(_MESSAGES, _CONFIG, spec)]

    rf = captured_kwargs.get("response_format", {})
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "result"
    assert rf["json_schema"]["schema"] == _SCHEMA


@pytest.mark.asyncio
async def test_ollama_json_schema_stream():
    adapter = OllamaProvider()
    spec = OutputSpec(json_schema=_SCHEMA)

    captured_payload: dict = {}

    async def aiter_lines_impl():
        yield json.dumps({"message": {"content": "{"}, "done": False})
        yield json.dumps({"message": {"content": ""}, "done": True})

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.aiter_lines = aiter_lines_impl
    fake_response.__aenter__ = AsyncMock(return_value=fake_response)
    fake_response.__aexit__ = AsyncMock(return_value=None)

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    def fake_stream(method, url, *, json=None, timeout=None):
        captured_payload.update(json or {})
        return fake_response

    fake_client.stream = fake_stream

    with patch("httpx.AsyncClient", return_value=fake_client):
        chunks = [c async for c in adapter.stream(_MESSAGES, _CONFIG, spec)]

    assert captured_payload.get("format") == _SCHEMA


@pytest.mark.asyncio
async def test_anthropic_json_schema_stream():
    adapter = AnthropicProvider(api_key="sk-test")
    spec = OutputSpec(json_schema=_SCHEMA)

    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Give me a name."},
    ]

    captured_payload: dict = {}

    sse_lines = [
        'data: {"type": "content_block_delta", "delta": {"text": "{"}}',
        'data: {"type": "message_stop"}',
    ]

    class FakeSyncResponse:
        def raise_for_status(self): pass
        def iter_lines(self): return iter(sse_lines)
        def __enter__(self): return self
        def __exit__(self, *_): pass

    class FakeSyncStream:
        def __init__(self, method, url, *, json=None, headers=None, timeout=None):
            captured_payload.update(json or {})
        def __enter__(self): return FakeSyncResponse()
        def __exit__(self, *_): pass

    fake_sync_client = MagicMock()
    fake_sync_client.stream = FakeSyncStream
    fake_sync_client.__enter__ = MagicMock(return_value=fake_sync_client)
    fake_sync_client.__exit__ = MagicMock(return_value=None)

    with patch("httpx.Client", return_value=fake_sync_client):
        chunks = [c async for c in adapter.stream(messages, _CONFIG, spec)]

    system = captured_payload.get("system", "")
    assert "You are helpful." in system
    assert "<schema>" in system
