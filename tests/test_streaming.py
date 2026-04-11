"""Tests for engine.stream() — chunk delivery, session persistence, error propagation."""
from pathlib import Path

import pytest

from priest.engine import PriestEngine
from priest.errors import PriestError, ProviderNotRegisteredError
from priest.profile.loader import FilesystemProfileLoader
from priest.schema.request import PriestConfig, PriestRequest, SessionRef
from priest.session.memory_store import InMemorySessionStore
from tests.mock_adapter import MockAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"


def _make_engine(session_store=None, adapter_text="hello world"):
    return PriestEngine(
        profile_loader=FilesystemProfileLoader(FIXTURES),
        session_store=session_store,
        adapters={"mock": MockAdapter(text=adapter_text)},
    )


def _make_request(**kwargs) -> PriestRequest:
    defaults = dict(
        config=PriestConfig(provider="mock", model="test-model"),
        profile="default",
        prompt="Say something.",
    )
    defaults.update(kwargs)
    return PriestRequest(**defaults)


@pytest.mark.asyncio
async def test_stream_yields_chunks():
    """stream() yields text chunks in order."""
    engine = _make_engine(adapter_text="hello world foo")
    chunks = []
    async for chunk in engine.stream(_make_request()):
        chunks.append(chunk)

    assert chunks == ["hello", "world", "foo"]


@pytest.mark.asyncio
async def test_stream_reassembles_to_full_text():
    """Reassembled stream matches the expected full text."""
    engine = _make_engine(adapter_text="the quick brown fox")
    parts = []
    async for chunk in engine.stream(_make_request()):
        parts.append(chunk)

    assert " ".join(parts) == "the quick brown fox"


@pytest.mark.asyncio
async def test_stream_saves_session():
    """Session is persisted after stream completes.

    The mock yields one chunk per word (no spaces), so the reassembled
    assistant turn is the words concatenated — matching "".join(chunks).
    """
    store = InMemorySessionStore()
    engine = _make_engine(session_store=store, adapter_text="hello world")
    request = _make_request(session=SessionRef(id="stream-session", create_if_missing=True))

    chunks = []
    async for chunk in engine.stream(request):
        chunks.append(chunk)

    saved = await store.get("stream-session")
    assert saved is not None
    assert len(saved.turns) == 2
    assert saved.turns[0].role == "user"
    assert saved.turns[0].content == "Say something."
    assert saved.turns[1].role == "assistant"
    # Engine joins all chunks with "".join() — matches what was actually streamed
    assert saved.turns[1].content == "".join(chunks)


@pytest.mark.asyncio
async def test_stream_session_continues_across_calls():
    """A second stream call appends turns to the existing session."""
    store = InMemorySessionStore()
    engine = _make_engine(session_store=store, adapter_text="ok")

    r1 = _make_request(session=SessionRef(id="multi-stream", create_if_missing=True))
    async for _ in engine.stream(r1):
        pass

    r2 = _make_request(session=SessionRef(id="multi-stream", continue_existing=True))
    async for _ in engine.stream(r2):
        pass

    saved = await store.get("multi-stream")
    assert saved is not None
    assert len(saved.turns) == 4  # 2 turns per call


@pytest.mark.asyncio
async def test_stream_unknown_provider_raises():
    """stream() raises ProviderNotRegisteredError for unknown providers."""
    engine = _make_engine()
    request = _make_request(config=PriestConfig(provider="unknown", model="x"))
    with pytest.raises(ProviderNotRegisteredError):
        async for _ in engine.stream(request):
            pass


@pytest.mark.asyncio
async def test_stream_no_session_store_still_yields():
    """stream() works without a session store — no session is persisted."""
    engine = _make_engine(session_store=None, adapter_text="a b c")
    chunks = []
    async for chunk in engine.stream(_make_request()):
        chunks.append(chunk)

    assert chunks == ["a", "b", "c"]
