from __future__ import annotations

from abc import ABC, abstractmethod

from priest.session.model import Session


class SessionStore(ABC):
    """Abstract base for session persistence backends."""

    @abstractmethod
    async def create(
        self,
        profile_name: str,
        session_id: str | None = None,
        metadata: dict | None = None,
    ) -> Session:
        """Create and persist a new session, returning it.

        If session_id is provided it is used as-is. Otherwise a UUID is generated.
        """
        ...

    @abstractmethod
    async def get(self, session_id: str) -> Session | None:
        """Return the session with the given ID, or None if not found."""
        ...

    @abstractmethod
    async def save(self, session: Session) -> None:
        """Persist the current state of a session (including all turns)."""
        ...
