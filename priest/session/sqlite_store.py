from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from priest.session.model import Session, Turn, _utcnow
from priest.session.store import SessionStore

_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    profile_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
)
"""

_CREATE_TURNS = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL
)
"""

_ISO = "%Y-%m-%dT%H:%M:%S.%f+00:00"


def _dt_to_str(dt: datetime) -> str:
    return dt.strftime(_ISO)


def _str_to_dt(s: str) -> datetime:
    # Handle both with and without microseconds, with or without tz suffix
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f+00:00", "%Y-%m-%dT%H:%M:%S+00:00"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # Fallback: isoformat parse
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class SqliteSessionStore(SessionStore):
    """SQLite-backed session store.

    db_path is provided by the host application — no default is hardcoded.
    Call await store.init() before first use, or use it as an async context manager.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Open the database and create tables if they do not exist."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(_CREATE_SESSIONS)
        await self._db.execute(_CREATE_TURNS)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> SqliteSessionStore:
        await self.init()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SqliteSessionStore not initialized — call await store.init() first")
        return self._db

    async def create(self, profile_name: str, metadata: dict | None = None) -> Session:
        now = _utcnow()
        session = Session(
            id=str(uuid.uuid4()),
            profile_name=profile_name,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        db = self._conn()
        await db.execute(
            "INSERT INTO sessions (id, profile_name, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?)",
            (
                session.id,
                session.profile_name,
                _dt_to_str(session.created_at),
                _dt_to_str(session.updated_at),
                json.dumps(session.metadata),
            ),
        )
        await db.commit()
        return session

    async def get(self, session_id: str) -> Session | None:
        db = self._conn()
        async with db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None

        session = Session(
            id=row["id"],
            profile_name=row["profile_name"],
            created_at=_str_to_dt(row["created_at"]),
            updated_at=_str_to_dt(row["updated_at"]),
            metadata=json.loads(row["metadata"]),
        )

        async with db.execute(
            "SELECT role, content, timestamp FROM turns WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ) as cur:
            turn_rows = await cur.fetchall()

        session.turns = [
            Turn(
                role=t["role"],
                content=t["content"],
                timestamp=_str_to_dt(t["timestamp"]),
            )
            for t in turn_rows
        ]
        return session

    async def save(self, session: Session) -> None:
        db = self._conn()
        await db.execute(
            "UPDATE sessions SET updated_at = ?, metadata = ? WHERE id = ?",
            (_dt_to_str(session.updated_at), json.dumps(session.metadata), session.id),
        )
        # Re-insert all turns: delete existing and reinsert to keep things simple
        # for a first version. Turns are append-only in practice.
        await db.execute("DELETE FROM turns WHERE session_id = ?", (session.id,))
        for turn in session.turns:
            await db.execute(
                "INSERT INTO turns (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (session.id, turn.role, turn.content, _dt_to_str(turn.timestamp)),
            )
        await db.commit()
