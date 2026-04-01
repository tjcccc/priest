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
async def test_session_created_on_first_run():
    store = InMemorySessionStore()
    engine = _make_engine(session_store=store)

    # First run creates a new session
    session_id = "test-session-1"
    request = _make_request(session=SessionRef(id=session_id, create_if_missing=True))
    response = await engine.run(request)

    assert response.session is not None
    assert response.session.is_new is True
    assert response.session.turn_count == 2  # user + assistant

    # Session is persisted
    saved = await store.get(response.session.id)
    assert saved is not None
    assert len(saved.turns) == 2
    assert saved.turns[0].role == "user"
    assert saved.turns[1].role == "assistant"


@pytest.mark.asyncio
async def test_session_continued_across_runs():
    store = InMemorySessionStore()
    engine = _make_engine(session_store=store)

    # Create session on first run
    r1 = await engine.run(_make_request(
        session=SessionRef(id="s1", create_if_missing=True)
    ))
    session_id = r1.session.id

    # Second run continues it
    r2 = await engine.run(_make_request(
        session=SessionRef(id=session_id, continue_existing=True)
    ))
    assert r2.session is not None
    assert r2.session.is_new is False
    assert r2.session.turn_count == 4  # 2 from first run + 2 from second


@pytest.mark.asyncio
async def test_json_mode_parses_payload():
    engine = _make_engine(adapter_text='{"answer": 42}')
    request = _make_request(output=OutputSpec(mode="json", strict_json=True))
    response = await engine.run(request)

    assert response.ok
    assert response.json_payload == {"answer": 42}


@pytest.mark.asyncio
async def test_json_mode_invalid_json_sets_error():
    engine = _make_engine(adapter_text="not json at all")
    request = _make_request(output=OutputSpec(mode="json", strict_json=True))
    response = await engine.run(request)

    assert not response.ok
    assert response.error is not None
    assert "invalid json" in response.error.message.lower()


@pytest.mark.asyncio
async def test_extra_context_included_in_user_message():
    """Verify extra_context strings appear in the user message."""
    captured: list[dict] | None = None
    original_complete = MockAdapter.complete

    async def capturing_complete(self, messages, config, output_spec):
        nonlocal captured
        captured = messages
        return await original_complete(self, messages, config, output_spec)

    engine = _make_engine()
    request = _make_request(extra_context=["some extra info"])

    with patch.object(MockAdapter, "complete", capturing_complete):
        await engine.run(request)

    assert captured is not None
    user_msg = next(m for m in captured if m["role"] == "user")
    assert "some extra info" in user_msg["content"]


@pytest.mark.asyncio
async def test_system_context_appears_first_in_system_message():
    """Verify system_context is injected at the top of the system prompt."""
    captured: list[dict] | None = None
    original_complete = MockAdapter.complete

    async def capturing_complete(self, messages, config, output_spec):
        nonlocal captured
        captured = messages
        return await original_complete(self, messages, config, output_spec)

    engine = _make_engine()
    request = _make_request(system_context=["Today is 2026-04-01.", "App: priests"])

    with patch.object(MockAdapter, "complete", capturing_complete):
        await engine.run(request)

    assert captured is not None
    system_msg = next(m for m in captured if m["role"] == "system")
    content = system_msg["content"]
    assert "Today is 2026-04-01." in content
    # system_context must appear before profile rules
    assert content.index("Today is 2026-04-01.") < content.index("Do not make things up")
