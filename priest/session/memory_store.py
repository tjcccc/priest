from __future__ import annotations

import uuid

from priest.session.model import Session
from priest.session.store import SessionStore


class InMemorySessionStore(SessionStore):
    """Non-persistent session store for tests and ephemeral use."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    async def create(self, profile_name: str, metadata: dict | None = None) -> Session:
        session = Session(
            id=str(uuid.uuid4()),
            profile_name=profile_name,
            metadata=metadata or {},
        )
        self._sessions[session.id] = session
        return session

    async def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    async def save(self, session: Session) -> None:
        self._sessions[session.id] = session
