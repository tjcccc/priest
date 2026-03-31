from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
