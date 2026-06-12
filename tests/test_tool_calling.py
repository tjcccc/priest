"""Tool calling, tool loop, and stream_events tests (spec 2.4.0)."""

from pathlib import Path

import pytest

from priest.engine import PriestEngine
from priest.profile.context_builder import build_messages
from priest.profile.loader import FilesystemProfileLoader
from priest.schema.request import (
    AssistantToolTurn,
    OutputSpec,
    PriestConfig,
    PriestRequest,
    SessionRef,
    ToolCall,
    ToolDefinition,
    ToolResultTurn,
)
from priest.session.memory_store import InMemorySessionStore
from priest.tool_loop import ApprovalDecision, ToolExecutionResult, run_with_tools
from tests.mock_adapter import ScriptedAdapter, scripted_result

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"

READ_FILE_CALL = ToolCall(id="call_0", name="read_file", arguments={"path": "a.txt"})
READ_FILE_TOOL = ToolDefinition(name="read_file", description="Read a file")


def _engine(adapter, session_store=None) -> PriestEngine:
    return PriestEngine(
        profile_loader=FilesystemProfileLoader(FIXTURES),
        session_store=session_store,
        adapters={"mock": adapter},
    )


def _request(**kwargs) -> PriestRequest:
    defaults = dict(
        config=PriestConfig(provider="mock", model="test-model"),
        profile="default",
        prompt="Read a.txt",
        tools=[READ_FILE_TOOL],
    )
    defaults.update(kwargs)
    return PriestRequest(**defaults)


@pytest.mark.asyncio
async def test_tool_calls_surface_with_finished_reason():
    adapter = ScriptedAdapter([
        scripted_result(finish_reason="tool_calls", tool_calls=[READ_FILE_CALL]),
    ])
    response = await _engine(adapter).run(_request())

    assert response.ok
    assert response.tool_calls == [READ_FILE_CALL]
    assert response.execution.finished_reason == "tool_calls"
    # tools threaded into adapter options
    assert adapter.calls[0]["options"].tools == [READ_FILE_TOOL]


@pytest.mark.asyncio
async def test_forced_tool_calls_finish_reason():
    adapter = ScriptedAdapter([
        scripted_result(finish_reason="stop", tool_calls=[READ_FILE_CALL]),
    ])
    response = await _engine(adapter).run(_request())
    assert response.execution.finished_reason == "tool_calls"


def test_tool_exchange_replayed_after_user_message():
    loader = FilesystemProfileLoader(FIXTURES)
    messages = build_messages(
        profile=loader.load("default"),
        session=None,
        prompt="Read a.txt",
        context=[],
        memory=[],
        user_context=[],
        output_spec=OutputSpec(),
        tool_exchange=[
            AssistantToolTurn(text="", tool_calls=[READ_FILE_CALL]),
            ToolResultTurn(tool_call_id="call_0", name="read_file", content="file body"),
        ],
    )

    assert messages[-3]["role"] == "user"
    assert messages[-2]["role"] == "assistant"
    assert messages[-2]["tool_calls"][0]["name"] == "read_file"
    assert messages[-1] == {
        "role": "tool",
        "content": "file body",
        "tool_call_id": "call_0",
        "name": "read_file",
    }


@pytest.mark.asyncio
async def test_session_not_persisted_while_tool_calls_pending():
    store = InMemorySessionStore()
    adapter = ScriptedAdapter([
        scripted_result(finish_reason="tool_calls", tool_calls=[READ_FILE_CALL]),
        scripted_result(text="The file says hello.", finish_reason="stop"),
    ])
    engine = _engine(adapter, session_store=store)

    first = await engine.run(_request(session=SessionRef(id="s1")))
    assert first.tool_calls
    session = await store.get("s1")
    assert session is not None and len(session.turns) == 0

    second = await engine.run(_request(
        session=SessionRef(id="s1"),
        tool_exchange=[
            AssistantToolTurn(tool_calls=[READ_FILE_CALL]),
            ToolResultTurn(tool_call_id="call_0", name="read_file", content="hello"),
        ],
    ))
    assert second.text == "The file says hello."
    session = await store.get("s1")
    assert [t.role for t in session.turns] == ["user", "assistant"]
    assert session.turns[0].content == "Read a.txt"


@pytest.mark.asyncio
async def test_run_with_tools_executes_and_returns_final_response():
    adapter = ScriptedAdapter([
        scripted_result(finish_reason="tool_calls", tool_calls=[READ_FILE_CALL]),
        scripted_result(text="The file says hello.", finish_reason="stop"),
    ])
    executed: list[ToolCall] = []

    async def executor(call: ToolCall) -> ToolExecutionResult:
        executed.append(call)
        return ToolExecutionResult(content="hello")

    result = await run_with_tools(_engine(adapter), _request(), executor)

    assert executed == [READ_FILE_CALL]
    assert result.response.text == "The file says hello."
    assert not result.iteration_limit_reached
    assert result.exchange[0].kind == "assistant"
    assert result.exchange[1].kind == "tool_result"
    assert result.exchange[1].content == "hello"
    # second engine call replayed the exchange
    assert any(m.get("role") == "tool" for m in adapter.calls[1]["messages"])


@pytest.mark.asyncio
async def test_run_with_tools_denial_injects_error_result():
    adapter = ScriptedAdapter([
        scripted_result(finish_reason="tool_calls", tool_calls=[READ_FILE_CALL]),
        scripted_result(text="Understood.", finish_reason="stop"),
    ])
    executions = 0

    async def executor(call: ToolCall) -> ToolExecutionResult:
        nonlocal executions
        executions += 1
        return ToolExecutionResult(content="never")

    async def deny(call: ToolCall) -> ApprovalDecision:
        return ApprovalDecision(approved=False, reason="not allowed")

    result = await run_with_tools(_engine(adapter), _request(), executor, on_tool_call=deny)

    assert executions == 0
    denial = result.exchange[1]
    assert denial.is_error is True
    assert "not allowed" in denial.content


@pytest.mark.asyncio
async def test_run_with_tools_iteration_cap():
    adapter = ScriptedAdapter([
        scripted_result(finish_reason="tool_calls", tool_calls=[READ_FILE_CALL]),
    ])

    async def executor(call: ToolCall) -> ToolExecutionResult:
        return ToolExecutionResult(content="data")

    result = await run_with_tools(_engine(adapter), _request(), executor, max_iterations=3)

    assert result.iteration_limit_reached
    assert len(adapter.calls) == 3


@pytest.mark.asyncio
async def test_stream_events_fallback_wraps_plain_stream():
    from tests.mock_adapter import MockAdapter

    engine = _engine(MockAdapter(text="hello world"))
    events = [e async for e in engine.stream_events(_request(tools=[]))]

    deltas = [e.text for e in events if e.type == "text_delta"]
    assert deltas == ["hello", "world"]
    done = events[-1]
    assert done.type == "done"
    assert done.response.text == "helloworld"
    assert done.response.ok


@pytest.mark.asyncio
async def test_stream_events_session_persists_on_final_text_only():
    from tests.mock_adapter import MockAdapter

    store = InMemorySessionStore()
    engine = _engine(MockAdapter(text="final answer"), session_store=store)
    events = [e async for e in engine.stream_events(_request(tools=[], session=SessionRef(id="s2")))]

    assert events[-1].response.session is not None
    session = await store.get("s2")
    assert [t.role for t in session.turns] == ["user", "assistant"]
