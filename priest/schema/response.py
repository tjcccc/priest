from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from priest.schema.request import ToolCall


class UsageInfo(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None


class ExecutionInfo(BaseModel):
    provider: str
    model: str
    latency_ms: int | None = None
    profile: str
    finished_reason: Literal["stop", "length", "tool_calls", "error", "unknown"] | None = None


class SessionInfo(BaseModel):
    id: str
    is_new: bool = False
    turn_count: int = 0


class PriestError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class PriestResponse(BaseModel):
    # Raw text returned by the provider. Always populated on success.
    # Parsing (JSON, XML, etc.) is the app layer's responsibility.
    text: str | None = None
    # Tool calls requested by the model. Non-empty exactly when
    # execution.finished_reason is "tool_calls". The caller executes them and
    # re-runs with the results appended to request.tool_exchange.
    tool_calls: list[ToolCall] | None = None
    execution: ExecutionInfo
    usage: UsageInfo | None = None
    session: SessionInfo | None = None
    error: PriestError | None = None
    # Caller metadata echoed back from the request.
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None
