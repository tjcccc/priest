"""Conversation compaction + session turn window (spec 2.5.0 / 2.6.0)."""
from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator

import pytest

from priest.compactor import build_summary_messages, plan_compaction, should_compact
from priest.engine import PriestEngine
from priest.errors import SessionNotFoundError
from priest.profile.context_builder import build_messages
from priest.profile.loader import FilesystemProfileLoader
from priest.profile.model import Profile
from priest.providers.base import AdapterCallOptions, AdapterResult, AdapterStreamEvent, ProviderAdapter
from priest.schema.request import OutputSpec, PriestConfig, PriestRequest, SessionRef
from priest.schema.request import AssistantToolTurn, ToolResultTurn  # noqa: F401
from priest.session.memory_store import InMemorySessionStore
from priest.session.model import COMPACTION_METADATA_KEY, Session, Turn

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"
SUMMARY_MARKER = "compress prior conversation"


def _turn(role: str, content: str) -> Turn:
    return Turn(role=role, content=content)  # type: ignore[arg-type]


class ProgrammableAdapter(ProviderAdapter):
    """Reports a fixed input size on chat turns (to drive the compaction trigger)
    and a short summary on the summarization call, recognized by the Compactor
    system prompt. Records every messages array it receives."""

    provider_name = "mock"

    def __init__(self, input_tokens: int, summary_text: str = "SUMMARY") -> None:
        self._input_tokens = input_tokens
        self._summary_text = summary_text
        self.calls: list[list[dict]] = []

    def _is_summary(self, messages: list[dict]) -> bool:
        sys = messages[0].get("content") if messages else None
        return isinstance(sys, str) and SUMMARY_MARKER in sys

    async def complete(self, messages, config, output_spec, options=None) -> AdapterResult:
        self.calls.append(messages)
        summary = self._is_summary(messages)
        return AdapterResult(
            text=self._summary_text if summary else "assistant reply",
            raw=None,
            finish_reason="stop",
            input_tokens=5 if summary else self._input_tokens,
            output_tokens=5,
        )

    async def stream_events(self, messages, config, output_spec, options=None) -> AsyncGenerator[AdapterStreamEvent, None]:
        self.calls.append(messages)
        yield AdapterStreamEvent(type="text_delta", text="assistant reply")
        yield AdapterStreamEvent(type="usage", input_tokens=self._input_tokens, output_tokens=5)
        yield AdapterStreamEvent(type="finish", finish_reason="stop")


def _engine(store: InMemorySessionStore, adapter: ProviderAdapter) -> PriestEngine:
    return PriestEngine(
        profile_loader=FilesystemProfileLoader(FIXTURES),
        session_store=store,
        adapters={"mock": adapter},
    )


_BUDGET_CONFIG = PriestConfig(provider="mock", model="test-model", max_context_tokens=100, compaction_keep_turns=2)
_NO_BUDGET = PriestConfig(provider="mock", model="test-model")


# ---------------------------------------------------------------------------
# Compactor (pure)
# ---------------------------------------------------------------------------

def test_should_compact_off_without_budget_or_measured_turn():
    assert should_compact(10_000, None) is False
    assert should_compact(10_000, 0) is False
    assert should_compact(None, 1000) is False


def test_should_compact_fires_only_above_80_percent():
    assert should_compact(799, 1000) is False
    assert should_compact(801, 1000) is True


def test_plan_compaction_none_while_history_fits():
    turns = [_turn("user", "a"), _turn("assistant", "b")]
    assert plan_compaction(turns, 0, 2) is None


def test_plan_compaction_folds_before_tail_and_advances():
    turns = [_turn("user", "u1"), _turn("assistant", "a1"), _turn("user", "u2"), _turn("assistant", "a2")]
    plan = plan_compaction(turns, 0, 2)
    assert plan is not None
    assert plan.summarized_through == 2
    assert [t.content for t in plan.to_summarize] == ["u1", "a1"]


def test_plan_compaction_recursive_only_folds_after_summarized():
    turns = [
        _turn("user", "u1"), _turn("assistant", "a1"),
        _turn("user", "u2"), _turn("assistant", "a2"),
        _turn("user", "u3"), _turn("assistant", "a3"),
    ]
    plan = plan_compaction(turns, 2, 2)
    assert plan is not None
    assert plan.summarized_through == 4
    assert [t.content for t in plan.to_summarize] == ["u2", "a2"]


def test_build_summary_messages_merges_existing_and_includes_new_turns():
    messages = build_summary_messages("prior synopsis", [_turn("user", "hello"), _turn("assistant", "hi there")])
    assert SUMMARY_MARKER in messages[0]["content"]
    assert "prior synopsis" in messages[1]["content"]
    assert "hello" in messages[1]["content"]
    assert "hi there" in messages[1]["content"]


# ---------------------------------------------------------------------------
# Engine compaction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compacts_over_budget_chat_and_replays_summary_plus_tail():
    store = InMemorySessionStore()
    adapter = ProgrammableAdapter(200)
    engine = _engine(store, adapter)

    for prompt in ["msg1", "msg2", "msg3"]:
        await engine.run(PriestRequest(config=_BUDGET_CONFIG, prompt=prompt, session=SessionRef(id="s")))

    session = await store.get("s")
    assert session is not None
    assert session.get_compaction().summary == "SUMMARY"

    # A summarization call happened.
    assert any(isinstance(m[0].get("content"), str) and SUMMARY_MARKER in m[0]["content"] for m in adapter.calls)

    # The most recent chat turn carries the summary in system and dropped the folded first turn.
    last_chat = next(m for m in reversed(adapter.calls) if not (isinstance(m[0].get("content"), str) and SUMMARY_MARKER in m[0]["content"]))
    assert "## Conversation so far (summary)" in last_chat[0]["content"]
    assert "SUMMARY" in last_chat[0]["content"]
    assert not any(m.get("content") == "msg1" for m in last_chat)


@pytest.mark.asyncio
async def test_compaction_state_persists_with_camelcase_keys():
    """The __compaction metadata is a cross-SDK contract — camelCase keys verbatim."""
    store = InMemorySessionStore()
    engine = _engine(store, ProgrammableAdapter(200))

    for prompt in ["msg1", "msg2", "msg3"]:
        await engine.run(PriestRequest(config=_BUDGET_CONFIG, prompt=prompt, session=SessionRef(id="s")))

    session = await store.get("s")
    assert session is not None
    raw = session.metadata[COMPACTION_METADATA_KEY]
    assert "summary" in raw
    assert "summarizedThrough" in raw
    assert "lastInputTokens" in raw
    # snake_case must NOT leak into the persisted wire form.
    assert "summarized_through" not in raw
    assert "last_input_tokens" not in raw


@pytest.mark.asyncio
async def test_compaction_state_survives_sqlite_round_trip(tmp_path):
    """Cross-SDK interop: state written as camelCase JSON must read back from a
    fresh store (and from the raw persisted bytes) — the contract Rust/.NET/Swift
    must also satisfy."""
    import sqlite3

    from priest.session.sqlite_store import SqliteSessionStore

    db = tmp_path / "sessions.db"
    store = SqliteSessionStore(db)
    await store.init()
    engine = _engine(store, ProgrammableAdapter(200))

    for prompt in ["msg1", "msg2", "msg3"]:
        await engine.run(PriestRequest(config=_BUDGET_CONFIG, prompt=prompt, session=SessionRef(id="s")))

    # Reopen a fresh store on the same DB — forces a JSON deserialize from disk.
    fresh = SqliteSessionStore(db)
    await fresh.init()
    session = await fresh.get("s")
    assert session is not None
    comp = session.get_compaction()
    assert comp.summary == "SUMMARY"
    assert comp.summarized_through == 2

    # Assert on the actual persisted bytes: camelCase only, no snake_case leak.
    raw_text = sqlite3.connect(db).execute("SELECT metadata FROM sessions WHERE id = 's'").fetchone()[0]
    assert '"summarizedThrough"' in raw_text
    assert '"summarized_through"' not in raw_text


@pytest.mark.asyncio
async def test_still_compacts_when_tools_offered_but_not_invoked():
    store = InMemorySessionStore()
    engine = _engine(store, ProgrammableAdapter(200))
    tools = [{"name": "web_search", "description": "search", "parameters": {"type": "object"}}]

    for prompt in ["msg1", "msg2", "msg3"]:
        await engine.run(PriestRequest(config=_BUDGET_CONFIG, prompt=prompt, session=SessionRef(id="s"), tools=tools))

    session = await store.get("s")
    assert session is not None and session.get_compaction().summary == "SUMMARY"


@pytest.mark.asyncio
async def test_does_not_record_trigger_when_tool_exchange_replayed():
    store = InMemorySessionStore()
    engine = _engine(store, ProgrammableAdapter(200))
    tool_exchange = [ToolResultTurn(tool_call_id="c1", name="web_search", content="big results")]

    await engine.run(PriestRequest(config=_BUDGET_CONFIG, prompt="msg1", session=SessionRef(id="s"), tool_exchange=tool_exchange))

    session = await store.get("s")
    assert session is not None
    assert session.get_compaction().last_input_tokens is None


@pytest.mark.asyncio
async def test_compacts_over_streaming_path():
    store = InMemorySessionStore()
    engine = _engine(store, ProgrammableAdapter(200))

    for prompt in ["msg1", "msg2", "msg3"]:
        async for _ in engine.stream_events(PriestRequest(config=_BUDGET_CONFIG, prompt=prompt, session=SessionRef(id="s"))):
            pass

    session = await store.get("s")
    assert session is not None and session.get_compaction().summary == "SUMMARY"


@pytest.mark.asyncio
async def test_never_compacts_without_budget():
    store = InMemorySessionStore()
    adapter = ProgrammableAdapter(200)
    engine = _engine(store, adapter)

    for prompt in ["msg1", "msg2", "msg3", "msg4"]:
        await engine.run(PriestRequest(config=_NO_BUDGET, prompt=prompt, session=SessionRef(id="s")))

    session = await store.get("s")
    assert session is not None and session.get_compaction().summary is None
    assert not any(isinstance(m[0].get("content"), str) and SUMMARY_MARKER in m[0]["content"] for m in adapter.calls)


@pytest.mark.asyncio
async def test_compact_session_folds_on_demand_and_reports_coverage():
    store = InMemorySessionStore()
    engine = _engine(store, ProgrammableAdapter(10))  # small input — no auto-compaction

    for prompt in ["msg1", "msg2", "msg3"]:
        await engine.run(PriestRequest(config=_NO_BUDGET, prompt=prompt, session=SessionRef(id="s")))
    assert (await store.get("s")).get_compaction().summary is None

    result = await engine.compact_session("s", PriestConfig(provider="mock", model="test-model", compaction_keep_turns=2))
    assert result["compacted"] is True
    assert result["summarized_through"] == 4  # 6 turns − keep 2
    assert (await store.get("s")).get_compaction().summary == "SUMMARY"


@pytest.mark.asyncio
async def test_compact_session_raises_for_unknown_session():
    engine = _engine(InMemorySessionStore(), ProgrammableAdapter(10))
    with pytest.raises(SessionNotFoundError):
        await engine.compact_session("nope", PriestConfig(provider="mock", model="m"))


# ---------------------------------------------------------------------------
# Session turn window (spec 2.6.0)
# ---------------------------------------------------------------------------

def _profile() -> Profile:
    return Profile(name="default", identity="", rules="", custom="", memories=[], meta={})


def _session_with(n: int) -> Session:
    session = Session(id="s", profile_name="default")
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        session.turns.append(_turn(role, f"turn-{i}"))
    return session


def _replayed(messages: list[dict]) -> list[str]:
    # All non-system messages except the trailing current-prompt user message.
    body = [m for m in messages if m["role"] != "system"]
    return [m["content"] for m in body[:-1]]


def test_replays_all_turns_when_window_unset():
    msgs = build_messages(_profile(), _session_with(6), "Hi", [], [], [], OutputSpec())
    assert _replayed(msgs) == ["turn-0", "turn-1", "turn-2", "turn-3", "turn-4", "turn-5"]


def test_replays_only_last_n_turns():
    msgs = build_messages(_profile(), _session_with(6), "Hi", [], [], [], OutputSpec(), session_context_turns=2)
    assert _replayed(msgs) == ["turn-4", "turn-5"]


def test_replays_no_turns_when_window_is_zero():
    msgs = build_messages(_profile(), _session_with(6), "Hi", [], [], [], OutputSpec(), session_context_turns=0)
    assert _replayed(msgs) == []


def test_snaps_odd_window_down_to_user_turn():
    # 8 turns (u0,a1,…); window 5 → naive start index 3 (assistant). Snap to 2 (user).
    msgs = build_messages(_profile(), _session_with(8), "Hi", [], [], [], OutputSpec(), session_context_turns=5)
    first_replayed = next(m for m in msgs if m["role"] != "system")
    assert first_replayed["role"] == "user"
    assert _replayed(msgs) == ["turn-2", "turn-3", "turn-4", "turn-5", "turn-6", "turn-7"]


def test_window_never_unhides_summarized_turns():
    session = _session_with(6)
    session.apply_compaction("earlier conversation summary", 4)  # turns[0..4) folded away
    msgs = build_messages(_profile(), session, "Hi", [], [], [], OutputSpec(), session_context_turns=5)
    assert _replayed(msgs) == ["turn-4", "turn-5"]
    assert "earlier conversation summary" in msgs[0]["content"]
