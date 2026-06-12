from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import AsyncGenerator

from priest.errors import (
    ErrorCode,
    PriestError,
    ProviderNotRegisteredError,
    SessionNotFoundError,
)
from priest.profile.context_builder import build_messages
from priest.profile.loader import ProfileLoader
from priest.providers.base import AdapterCallOptions, ProviderAdapter
from priest.schema.request import PriestRequest, ToolCall
from priest.schema.response import (
    ExecutionInfo,
    PriestError as PriestErrorModel,
    PriestResponse,
    SessionInfo,
    UsageInfo,
)
from priest.session.store import SessionStore

logger = logging.getLogger(__name__)


@dataclass
class PriestStreamEvent:
    """Engine-level structured streaming event (spec 2.4.0).

    type is one of: text_delta, tool_call_start, tool_call_delta,
    tool_call_end, usage, done. The terminal event is always 'done' carrying
    the full PriestResponse (including tool_calls, usage, and error state).
    """
    type: str
    text: str | None = None
    index: int | None = None
    id: str | None = None
    name: str | None = None
    arguments_delta: str | None = None
    tool_call: ToolCall | None = None
    usage: UsageInfo | None = None
    response: PriestResponse | None = None


def _call_options(request: PriestRequest) -> AdapterCallOptions | None:
    if not request.tools:
        return None
    return AdapterCallOptions(tools=request.tools, tool_choice=request.tool_choice)


class PriestEngine:
    """Orchestrates a single AI run.

    The engine is stateless per-run — it holds no mutable state between calls.
    Profile caching, if needed, should be implemented in the host app's
    ProfileLoader wrapper.
    """

    def __init__(
        self,
        profile_loader: ProfileLoader,
        session_store: SessionStore | None = None,
        adapters: dict[str, ProviderAdapter] | None = None,
    ) -> None:
        self._profile_loader = profile_loader
        self._session_store = session_store
        self._adapters: dict[str, ProviderAdapter] = adapters or {}

    async def run(self, request: PriestRequest) -> PriestResponse:
        start_ms = int(time.monotonic() * 1000)

        # --- Resolve provider adapter ---
        adapter = self._adapters.get(request.config.provider)
        if adapter is None:
            raise ProviderNotRegisteredError(request.config.provider)

        # --- Load profile ---
        profile = self._profile_loader.load(request.profile)

        # --- Session handling ---
        session = None
        session_info: SessionInfo | None = None
        is_new_session = False

        if request.session is not None and self._session_store is not None:
            session_ref = request.session
            if session_ref.continue_existing:
                session = await self._session_store.get(session_ref.id)
                if session is None:
                    if session_ref.create_if_missing:
                        # Honor the caller's ID — session is created with it,
                        # making create_if_missing idempotent on the same ID.
                        session = await self._session_store.create(
                            profile_name=request.profile,
                            session_id=session_ref.id,
                        )
                        is_new_session = True
                    else:
                        raise SessionNotFoundError(session_ref.id)
            else:
                session = await self._session_store.create(
                    profile_name=request.profile,
                )
                is_new_session = True

        # --- Build message list ---
        messages = build_messages(
            profile=profile,
            session=session,
            prompt=request.prompt,
            context=request.context,
            memory=request.memory,
            user_context=request.user_context,
            output_spec=request.output,
            images=request.images or None,
            max_system_chars=request.config.max_system_chars,
            tool_exchange=request.tool_exchange or None,
        )

        # --- Call provider ---
        error_model: PriestErrorModel | None = None
        text: str | None = None
        tool_calls: list[ToolCall] | None = None
        finish_reason: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None

        try:
            result = await adapter.complete(
                messages=messages,
                config=request.config,
                output_spec=request.output,
                options=_call_options(request),
            )
            text = result.text
            tool_calls = result.tool_calls or None
            finish_reason = result.finish_reason
            input_tokens = result.input_tokens
            output_tokens = result.output_tokens
            if tool_calls and finish_reason != "tool_calls":
                finish_reason = "tool_calls"

        except PriestError as exc:
            finish_reason = "error"
            error_model = PriestErrorModel(
                code=exc.code,
                message=exc.message,
                details={k: str(v) for k, v in exc.details.items()},
            )
            logger.warning("Provider error: %s", exc)

        # --- Update session with new turns ---
        # Tool-call iterations are turn-local: persist only when the model
        # produced a final answer (spec behavior/tool-calling.md).
        if session is not None and self._session_store is not None and error_model is None:
            if not tool_calls:
                session.append_turn("user", request.prompt)
                if text is not None:
                    session.append_turn("assistant", text)
                await self._session_store.save(session)
            session_info = SessionInfo(
                id=session.id,
                is_new=is_new_session,
                turn_count=len(session.turns),
            )

        latency_ms = int(time.monotonic() * 1000) - start_ms

        usage: UsageInfo | None = None
        if input_tokens is not None or output_tokens is not None:
            total = (input_tokens or 0) + (output_tokens or 0)
            usage = UsageInfo(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total if total > 0 else None,
            )

        return PriestResponse(
            text=text,
            tool_calls=tool_calls,
            execution=ExecutionInfo(
                provider=request.config.provider,
                model=request.config.model,
                latency_ms=latency_ms,
                profile=request.profile,
                finished_reason=finish_reason,  # type: ignore[arg-type]
            ),
            usage=usage,
            session=session_info,
            error=error_model,
            metadata=request.metadata,
        )

    async def stream(self, request: PriestRequest) -> AsyncGenerator[str, None]:
        """Yield text chunks as they arrive from the provider.

        Implemented as a filter over stream_events(): text deltas pass
        through, and a provider error in the terminal done event is re-raised
        as a PriestError (preserving the legacy stream() contract).

        Note: unlike run(), stream() yields only raw text chunks — there is no
        final PriestResponse. Use stream_events() for tool calls or metadata.
        """
        async for event in self.stream_events(request):
            if event.type == "text_delta" and event.text:
                yield event.text
            elif event.type == "done" and event.response is not None and event.response.error is not None:
                err = event.response.error
                raise PriestError(
                    ErrorCode(err.code) if err.code in ErrorCode.__members__ else ErrorCode.INTERNAL_ERROR,
                    err.message,
                    **{k: str(v) for k, v in err.details.items()},
                )

    async def stream_events(self, request: PriestRequest) -> AsyncGenerator[PriestStreamEvent, None]:
        """Yield structured streaming events (spec 2.4.0).

        Yields text deltas, tool-call progress, usage refinements, and a
        terminal 'done' event carrying the full PriestResponse. Provider
        errors surface in done.response.error rather than being raised,
        matching run() semantics. PROVIDER_NOT_REGISTERED and
        SESSION_NOT_FOUND still raise.

        Cancellation: cancel the consuming asyncio task; CancelledError
        propagates and the session is not saved.
        """
        start_ms = int(time.monotonic() * 1000)

        adapter = self._adapters.get(request.config.provider)
        if adapter is None:
            raise ProviderNotRegisteredError(request.config.provider)

        profile = self._profile_loader.load(request.profile)

        session = None
        is_new_session = False

        if request.session is not None and self._session_store is not None:
            session_ref = request.session
            if session_ref.continue_existing:
                session = await self._session_store.get(session_ref.id)
                if session is None:
                    if session_ref.create_if_missing:
                        session = await self._session_store.create(
                            profile_name=request.profile,
                            session_id=session_ref.id,
                        )
                        is_new_session = True
                    else:
                        raise SessionNotFoundError(session_ref.id)
            else:
                session = await self._session_store.create(profile_name=request.profile)
                is_new_session = True

        messages = build_messages(
            profile=profile,
            session=session,
            prompt=request.prompt,
            context=request.context,
            memory=request.memory,
            user_context=request.user_context,
            output_spec=request.output,
            images=request.images or None,
            max_system_chars=request.config.max_system_chars,
            tool_exchange=request.tool_exchange or None,
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        finish_reason: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        error_model: PriestErrorModel | None = None

        try:
            async for event in adapter.stream_events(
                messages, request.config, request.output, _call_options(request)
            ):
                if event.type == "text_delta" and event.text:
                    text_parts.append(event.text)
                    yield PriestStreamEvent(type="text_delta", text=event.text)
                elif event.type == "tool_call_start":
                    yield PriestStreamEvent(
                        type="tool_call_start", index=event.index, id=event.id, name=event.name
                    )
                elif event.type == "tool_call_delta":
                    yield PriestStreamEvent(
                        type="tool_call_delta", index=event.index, arguments_delta=event.arguments_delta
                    )
                elif event.type == "tool_call_end" and event.tool_call is not None:
                    tool_calls.append(event.tool_call)
                    yield PriestStreamEvent(
                        type="tool_call_end", index=event.index, tool_call=event.tool_call
                    )
                elif event.type == "usage":
                    input_tokens = event.input_tokens if event.input_tokens is not None else input_tokens
                    output_tokens = event.output_tokens if event.output_tokens is not None else output_tokens
                    yield PriestStreamEvent(type="usage", usage=_build_usage(input_tokens, output_tokens))
                elif event.type == "finish":
                    finish_reason = event.finish_reason or finish_reason
        except PriestError as exc:
            finish_reason = "error"
            error_model = PriestErrorModel(
                code=exc.code,
                message=exc.message,
                details={k: str(v) for k, v in exc.details.items()},
            )
            logger.warning("Provider error during stream: %s", exc)

        text = "".join(text_parts) if text_parts else None
        if tool_calls and finish_reason != "error":
            finish_reason = "tool_calls"

        session_info: SessionInfo | None = None
        if session is not None and self._session_store is not None and error_model is None:
            if not tool_calls and text is not None:
                session.append_turn("user", request.prompt)
                session.append_turn("assistant", text)
                await self._session_store.save(session)
            session_info = SessionInfo(
                id=session.id,
                is_new=is_new_session,
                turn_count=len(session.turns),
            )

        response = PriestResponse(
            text=text,
            tool_calls=tool_calls or None,
            execution=ExecutionInfo(
                provider=request.config.provider,
                model=request.config.model,
                latency_ms=int(time.monotonic() * 1000) - start_ms,
                profile=request.profile,
                finished_reason=finish_reason,  # type: ignore[arg-type]
            ),
            usage=_build_usage(input_tokens, output_tokens),
            session=session_info,
            error=error_model,
            metadata=request.metadata,
        )
        yield PriestStreamEvent(type="done", response=response)


def _build_usage(input_tokens: int | None, output_tokens: int | None) -> UsageInfo | None:
    if input_tokens is None and output_tokens is None:
        return None
    total = (input_tokens or 0) + (output_tokens or 0)
    return UsageInfo(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total if total > 0 else None,
    )
