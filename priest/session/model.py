from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Conversation-compaction state (spec 2.5.0) is persisted inside session
# `metadata` under this reserved key, so the SQLite schema and cross-SDK
# interop are unchanged (a pre-2.5 SDK simply ignores the key). The stored
# object uses these EXACT camelCase field names — a cross-SDK contract; see
# spec/behavior/session-lifecycle.md.
COMPACTION_METADATA_KEY = "__compaction"


@dataclass
class CompactionState:
    """Decoded view of session.metadata["__compaction"] (spec 2.5.0)."""
    # Running synopsis covering turns[0 .. summarized_through).
    summary: str | None = None
    # Number of leading turns folded into `summary` (index into turns).
    summarized_through: int = 0
    # Provider-reported input tokens of the most recent measured (chat) turn —
    # the compaction trigger signal.
    last_input_tokens: int | None = None
    # ISO-8601 timestamp of the last compaction-state update.
    updated_at: str | None = None


@dataclass
class Turn:
    role: Literal["user", "assistant"]
    content: str
    timestamp: datetime = field(default_factory=_utcnow)


@dataclass
class Session:
    id: str
    profile_name: str
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    turns: list[Turn] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def append_turn(self, role: Literal["user", "assistant"], content: str) -> None:
        self.turns.append(Turn(role=role, content=content))
        self.updated_at = _utcnow()

    # ---- Conversation compaction (spec 2.5.0) ----

    def get_compaction(self) -> CompactionState:
        """Read compaction state from metadata. Empty state when unset."""
        raw = self.metadata.get(COMPACTION_METADATA_KEY)
        if not isinstance(raw, dict):
            return CompactionState()
        return CompactionState(
            summary=raw.get("summary"),
            summarized_through=raw.get("summarizedThrough", 0) or 0,
            last_input_tokens=raw.get("lastInputTokens"),
            updated_at=raw.get("updatedAt"),
        )

    def _set_compaction(self, state: CompactionState) -> None:
        # Serialize with the camelCase wire keys; omit None fields.
        stored: dict = {"summarizedThrough": state.summarized_through}
        if state.summary is not None:
            stored["summary"] = state.summary
        if state.last_input_tokens is not None:
            stored["lastInputTokens"] = state.last_input_tokens
        if state.updated_at is not None:
            stored["updatedAt"] = state.updated_at
        self.metadata[COMPACTION_METADATA_KEY] = stored
        self.updated_at = _utcnow()

    def record_input_tokens(self, tokens: int | None) -> None:
        """Record the most recent turn's input size (the compaction trigger signal)."""
        if tokens is None:
            return
        state = self.get_compaction()
        state.last_input_tokens = tokens
        self._set_compaction(state)

    def apply_compaction(self, summary: str, summarized_through: int) -> None:
        """Fold turns[0 .. summarized_through) into `summary`; raw turns stay intact."""
        state = self.get_compaction()
        state.summary = summary
        state.summarized_through = summarized_through
        state.updated_at = _utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
        self._set_compaction(state)
