from __future__ import annotations

import logging
import time
from typing import AsyncGenerator

from priest.errors import (
    PriestError,
    ProviderNotRegisteredError,
    SessionNotFoundError,
)
from priest.profile.context_builder import build_messages
from priest.profile.loader import ProfileLoader
from priest.providers.base import ProviderAdapter
from priest.schema.request import PriestRequest
from priest.schema.response import (
    ExecutionInfo,
    PriestError as PriestErrorModel,
    PriestResponse,
    SessionInfo,
    UsageInfo,
)
from priest.session.store import SessionStore

logger = logging.getLogger(__name__)


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
        )

        # --- Call provider ---
        error_model: PriestErrorModel | None = None
        text: str | None = None
        finish_reason: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None

        try:
            result = await adapter.complete(
                messages=messages,
                config=request.config,
                output_spec=request.output,
            )
            text = result.text
            finish_reason = result.finish_reason
            input_tokens = result.input_tokens
            output_tokens = result.output_tokens

        except PriestError as exc:
            finish_reason = "error"
            error_model = PriestErrorModel(
                code=exc.code,
                message=exc.message,
                details={k: str(v) for k, v in exc.details.items()},
            )
            logger.warning("Provider error: %s", exc)

        # --- Update session with new turns ---
        if session is not None and self._session_store is not None and error_model is None:
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

        Session is saved automatically after the stream completes.
        Raises PriestError subclasses on provider failure.

        Note: unlike run(), stream() yields only raw text chunks — there is no
        final PriestResponse. Usage stats, latency, and session info are not
        returned. If you need structured metadata, use run() instead.
        """
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
        )

        parts: list[str] = []
        had_error = False
        try:
            async for chunk in adapter.stream(messages, request.config, request.output):
                parts.append(chunk)
                yield chunk
        except PriestError as exc:
            had_error = True
            logger.warning("Provider error during stream: %s", exc)
            raise
        finally:
            if not had_error and parts and session is not None and self._session_store is not None:
                full_text = "".join(parts)
                session.append_turn("user", request.prompt)
                session.append_turn("assistant", full_text)
                await self._session_store.save(session)
