from pathlib import Path
from unittest.mock import patch

import pytest

from priest.engine import PriestEngine
from priest.errors import ProviderNotRegisteredError
from priest.profile.loader import FilesystemProfileLoader
from priest.schema.request import OutputSpec, PriestConfig, PriestRequest, SessionRef
from priest.session.memory_store import InMemorySessionStore
from tests.mock_adapter import MockAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"


def _make_engine(session_store=None, adapter_text="hello"):
    return PriestEngine(
        profile_loader=FilesystemProfileLoader(FIXTURES),
        session_store=session_store,
        adapters={"mock": MockAdapter(text=adapter_text)},
    )


def _make_request(**kwargs) -> PriestRequest:
    defaults = dict(
        config=PriestConfig(provider="mock", model="test-model"),
        profile="default",
        prompt="Say hello.",
    )
    defaults.update(kwargs)
    return PriestRequest(**defaults)


@pytest.mark.asyncio
async def test_basic_run_returns_response():
    engine = _make_engine()
    response = await engine.run(_make_request())

    assert response.ok
    assert response.text == "hello"
    assert response.execution.provider == "mock"
    assert response.execution.model == "test-model"
    assert response.execution.profile == "default"
    assert response.execution.finished_reason == "stop"
    assert response.usage is not None
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 5
    assert response.usage.total_tokens == 15
    assert response.session is None


@pytest.mark.asyncio
async def test_unknown_provider_raises():
    engine = _make_engine()
    request = _make_request(config=PriestConfig(provider="unknown", model="x"))
    with pytest.raises(ProviderNotRegisteredError):
        await engine.run(request)


@pytest.mark.asyncio
async def test_metadata_echoed():
    engine = _make_engine()
    request = _make_request(metadata={"req_id": "abc123"})
    response = await engine.run(request)
    assert response.metadata == {"req_id": "abc123"}


@pytest.mark.asyncio
async def test_session_created_with_caller_id():
    """Session is created using the caller-provided ID, not a generated UUID."""
    store = InMemorySessionStore()
    engine = _make_engine(session_store=store)

    request = _make_request(session=SessionRef(id="my-session", create_if_missing=True))
    response = await engine.run(request)

    assert response.session is not None
    assert response.session.id == "my-session"
    assert response.session.is_new is True
    assert response.session.turn_count == 2

    saved = await store.get("my-session")
    assert saved is not None
    assert len(saved.turns) == 2
    assert saved.turns[0].role == "user"
    assert saved.turns[1].role == "assistant"


@pytest.mark.asyncio
async def test_session_continued_across_runs():
    store = InMemorySessionStore()
    engine = _make_engine(session_store=store)

    r1 = await engine.run(_make_request(
        session=SessionRef(id="s1", create_if_missing=True)
    ))
    assert r1.session.id == "s1"

    r2 = await engine.run(_make_request(
        session=SessionRef(id="s1", continue_existing=True)
    ))
    assert r2.session is not None
    assert r2.session.is_new is False
    assert r2.session.turn_count == 4  # 2 from first run + 2 from second


@pytest.mark.asyncio
async def test_json_format_returns_raw_text():
    """JSON format mode returns raw text — parsing is app layer's job."""
    engine = _make_engine(adapter_text='{"answer": 42}')
    request = _make_request(output=OutputSpec(provider_format="json"))
    response = await engine.run(request)

    assert response.ok
    assert response.text == '{"answer": 42}'


@pytest.mark.asyncio
async def test_prompt_format_injects_instruction():
    """prompt_format injects a format instruction into the system prompt."""
    captured: list[dict] | None = None
    original_complete = MockAdapter.complete

    async def capturing_complete(self, messages, config, output_spec):
        nonlocal captured
        captured = messages
        return await original_complete(self, messages, config, output_spec)

    engine = _make_engine()
    request = _make_request(output=OutputSpec(prompt_format="json"))

    with patch.object(MockAdapter, "complete", capturing_complete):
        await engine.run(request)

    assert captured is not None
    system_msg = next(m for m in captured if m["role"] == "system")
    assert "valid JSON" in system_msg["content"]


@pytest.mark.asyncio
async def test_provider_format_and_prompt_format_are_independent():
    """provider_format and prompt_format can be set independently."""
    engine = _make_engine(adapter_text="result")

    # prompt_format only — no provider hint
    r1 = await engine.run(_make_request(output=OutputSpec(prompt_format="json")))
    assert r1.ok

    # provider_format only — no prompt instruction
    r2 = await engine.run(_make_request(output=OutputSpec(provider_format="json")))
    assert r2.ok

    # both
    r3 = await engine.run(_make_request(output=OutputSpec(provider_format="json", prompt_format="json")))
    assert r3.ok

    # neither (default)
    r4 = await engine.run(_make_request(output=OutputSpec()))
    assert r4.ok


@pytest.mark.asyncio
async def test_user_context_included_in_user_message():
    """Verify user_context strings appear in the user message."""
    captured: list[dict] | None = None
    original_complete = MockAdapter.complete

    async def capturing_complete(self, messages, config, output_spec):
        nonlocal captured
        captured = messages
        return await original_complete(self, messages, config, output_spec)

    engine = _make_engine()
    request = _make_request(user_context=["some extra info"])

    with patch.object(MockAdapter, "complete", capturing_complete):
        await engine.run(request)

    assert captured is not None
    user_msg = next(m for m in captured if m["role"] == "user")
    assert "some extra info" in user_msg["content"]


@pytest.mark.asyncio
async def test_context_appears_first_in_system_message():
    """Verify `context` is injected at the top of the system prompt."""
    captured: list[dict] | None = None
    original_complete = MockAdapter.complete

    async def capturing_complete(self, messages, config, output_spec):
        nonlocal captured
        captured = messages
        return await original_complete(self, messages, config, output_spec)

    engine = _make_engine()
    request = _make_request(context=["Today is 2026-04-01.", "App: priests"])

    with patch.object(MockAdapter, "complete", capturing_complete):
        await engine.run(request)

    assert captured is not None
    system_msg = next(m for m in captured if m["role"] == "system")
    content = system_msg["content"]
    assert "Today is 2026-04-01." in content
    assert content.index("Today is 2026-04-01.") < content.index("Do not make things up")


@pytest.mark.asyncio
async def test_memory_field_injected_as_dynamic_section():
    """Verify the `memory` field produces a ## Memory section after profile memories."""
    captured: list[dict] | None = None
    original_complete = MockAdapter.complete

    async def capturing_complete(self, messages, config, output_spec):
        nonlocal captured
        captured = messages
        return await original_complete(self, messages, config, output_spec)

    engine = _make_engine()
    request = _make_request(memory=["Dynamic: user is on mobile."])

    with patch.object(MockAdapter, "complete", capturing_complete):
        await engine.run(request)

    assert captured is not None
    system_msg = next(m for m in captured if m["role"] == "system")
    content = system_msg["content"]
    assert "## Memory" in content
    assert "Dynamic: user is on mobile." in content


@pytest.mark.asyncio
async def test_max_system_chars_trims_memory():
    """Verify max_system_chars on PriestConfig trims dynamic memory tail-first."""
    captured: list[dict] | None = None
    original_complete = MockAdapter.complete

    async def capturing_complete(self, messages, config, output_spec):
        nonlocal captured
        captured = messages
        return await original_complete(self, messages, config, output_spec)

    engine = _make_engine()
    cfg = PriestConfig(provider="mock", model="test-model", max_system_chars=400)
    entries = [f"Entry-{i}-" + "x" * 100 for i in range(10)]
    request = _make_request(config=cfg, memory=entries)

    with patch.object(MockAdapter, "complete", capturing_complete):
        await engine.run(request)

    assert captured is not None
    system_msg = next(m for m in captured if m["role"] == "system")
    assert len(system_msg["content"]) <= 400
    # At least the first entry survives, last entry does not.
    assert "Entry-0-" in system_msg["content"]
    assert "Entry-9-" not in system_msg["content"]
