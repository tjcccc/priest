"""Generic caller-executes tool loop (spec 2.4.0, behavior/tool-calling.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from priest.engine import PriestEngine
from priest.schema.request import (
    AssistantToolTurn,
    PriestRequest,
    ToolCall,
    ToolExchangeTurn,
    ToolResultTurn,
)
from priest.schema.response import PriestResponse

_DEFAULT_MAX_ITERATIONS = 10

# Executes one tool call. Errors should be returned as content with is_error,
# not raised.
ToolExecutor = Callable[[ToolCall], Awaitable["ToolExecutionResult"]]

# Approval gate called before each execution. Returning approved=False injects
# a denial tool_result so the model can react.
ApprovalHook = Callable[[ToolCall], Awaitable["ApprovalDecision"]]


@dataclass
class ToolExecutionResult:
    content: str
    is_error: bool = False


@dataclass
class ApprovalDecision:
    approved: bool
    reason: str | None = None


@dataclass
class ToolLoopResult:
    # The final response — the first one without tool calls, or the last
    # iteration's response when the cap was hit or an error occurred.
    response: PriestResponse
    # Full tool exchange trace accumulated across iterations.
    exchange: list[ToolExchangeTurn] = field(default_factory=list)
    # True when the loop stopped because max_iterations was reached.
    iteration_limit_reached: bool = False


async def run_with_tools(
    engine: PriestEngine,
    request: PriestRequest,
    executor: ToolExecutor,
    *,
    on_tool_call: ApprovalHook | None = None,
    max_iterations: int = _DEFAULT_MAX_ITERATIONS,
) -> ToolLoopResult:
    """Run the request, execute tool calls through the caller-supplied
    executor, replay results via tool_exchange, and repeat until the model
    answers without tool calls or the iteration cap is hit.

    The library never chooses or sandboxes tools — policy belongs to the
    caller via the executor and the on_tool_call hook. Tool exchange turns are
    turn-local and never persisted in sessions.
    """
    max_iterations = max(1, max_iterations)
    exchange: list[ToolExchangeTurn] = list(request.tool_exchange)

    response: PriestResponse | None = None
    for _ in range(max_iterations):
        response = await engine.run(request.model_copy(update={"tool_exchange": exchange}))
        if not response.ok or not response.tool_calls:
            return ToolLoopResult(response=response, exchange=exchange)

        exchange.append(AssistantToolTurn(text=response.text, tool_calls=response.tool_calls))
        for call in response.tool_calls:
            decision = await on_tool_call(call) if on_tool_call else ApprovalDecision(approved=True)
            if not decision.approved:
                reason = f": {decision.reason}" if decision.reason else "."
                exchange.append(ToolResultTurn(
                    tool_call_id=call.id,
                    name=call.name,
                    content=f"Tool call denied by the caller{reason}",
                    is_error=True,
                ))
                continue
            result = await executor(call)
            exchange.append(ToolResultTurn(
                tool_call_id=call.id,
                name=call.name,
                content=result.content,
                is_error=result.is_error or None,
            ))

    assert response is not None  # max_iterations is clamped to >= 1
    return ToolLoopResult(response=response, exchange=exchange, iteration_limit_reached=True)
