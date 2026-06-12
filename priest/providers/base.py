from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from priest.schema.request import OutputSpec, PriestConfig, ToolCall, ToolChoice, ToolDefinition


@dataclass
class AdapterResult:
    """Raw result from a provider adapter before mapping to PriestResponse."""
    text: str | None
    raw: Any | None
    finish_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    # Tool calls requested by the model. None when there are none.
    tool_calls: list[ToolCall] | None = None


@dataclass
class AdapterStreamEvent:
    """One structured streaming event from an adapter (spec 2.4.0).

    type is one of: text_delta, tool_call_start, tool_call_delta,
    tool_call_end, usage, finish. Only the fields relevant to the type are
    populated.
    """
    type: str
    text: str | None = None
    index: int | None = None
    id: str | None = None
    name: str | None = None
    arguments_delta: str | None = None
    tool_call: ToolCall | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None


@dataclass
class AdapterCallOptions:
    """Per-call options threaded from the engine into adapters (spec 2.4.0)."""
    tools: list[ToolDefinition] = field(default_factory=list)
    tool_choice: ToolChoice | None = None


class ProviderAdapter(ABC):
    """Base class for all provider adapters.

    Adapters are thin translators: messages in, AdapterResult out.
    They do not inspect profile content, call back into the engine,
    or perform any business logic beyond sending the request and
    normalizing the response.

    Cancellation: Python uses native asyncio task cancellation — adapters must
    let asyncio.CancelledError propagate (the spec's REQUEST_ABORTED concept).
    """

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        config: PriestConfig,
        output_spec: OutputSpec,
        options: AdapterCallOptions | None = None,
    ) -> AdapterResult: ...

    async def stream(
        self,
        messages: list[dict],
        config: PriestConfig,
        output_spec: OutputSpec,
        options: AdapterCallOptions | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield text chunks as they arrive from the provider.

        Default implementation calls complete() and yields the full text as
        one chunk. Override in adapters that support native streaming.
        """
        result = await self.complete(messages, config, output_spec, options)
        if result.text:
            yield result.text

    async def stream_events(
        self,
        messages: list[dict],
        config: PriestConfig,
        output_spec: OutputSpec,
        options: AdapterCallOptions | None = None,
    ) -> AsyncGenerator[AdapterStreamEvent, None]:
        """Yield structured streaming events (spec 2.4.0).

        Default implementation wraps stream(): each text chunk becomes a
        text_delta event and a final finish event is synthesized. Override in
        adapters that can surface tool-call deltas and usage while streaming.
        """
        async for chunk in self.stream(messages, config, output_spec, options):
            yield AdapterStreamEvent(type="text_delta", text=chunk)
        yield AdapterStreamEvent(type="finish", finish_reason="stop")
