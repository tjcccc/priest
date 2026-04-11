# Session Lifecycle

This document defines session handling behavior for both `run()` and `stream()`, the SQLite persistence schema, and the canonical timestamp format.

Reference implementation: `priest/session/sqlite_store.py`, `priest/engine.py`

---

## Session handling decision tree

This decision tree runs at the start of every `run()` and `stream()` call. Variables used: `request.session` (a `SessionRef` or null), `session_store` (the store instance, may be null).

```
if request.session is null OR session_store is null:
    → no session handling
    → session_info in response = null

else:
    session_ref = request.session

    if session_ref.continue_existing is true:
        session = store.get(session_ref.id)

        if session is null:
            if session_ref.create_if_missing is true:
                session = store.create(profile_name=request.profile, session_id=session_ref.id)
                is_new = true
            else:
                → raise SESSION_NOT_FOUND (propagates as exception, not captured in response)
        else:
            is_new = false

    else (continue_existing is false):
        session = store.create(profile_name=request.profile)   // new UUID, session_ref.id ignored
        is_new = true
```

---

## Post-run session save (`run()`)

After a successful provider call (no error):

```
session.append_turn(role="user", content=request.prompt)
session.append_turn(role="assistant", content=response.text)
store.save(session)

response.session = SessionInfo(
    id = session.id,
    is_new = is_new,
    turn_count = len(session.turns)
)
```

If the provider returns an error (`response.error` is set), **the session is not saved** and `response.session` is null.

---

## Post-stream session save (`stream()`)

After all chunks have been yielded without error:

```
full_text = "".join(all_chunks)
session.append_turn(role="user", content=request.prompt)
session.append_turn(role="assistant", content=full_text)
store.save(session)
```

**`stream()` does not return a `PriestResponse`.** Session info, usage info, and execution info are not available after a stream call. If a provider error occurs during streaming, it propagates as a thrown exception and the session is **not** saved.

---

## SQLite schema

Implementations using SQLite **MUST** use the following exact DDL. This ensures cross-implementation interoperability (a session written by Python can be read by Swift and vice versa).

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    profile_name TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    metadata     TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    timestamp  TEXT NOT NULL
);
```

**Required PRAGMA on open:**

```sql
PRAGMA journal_mode=WAL;
```

---

## Timestamp format

### Write format (canonical)

All timestamps stored in SQLite **MUST** be written in this exact format:

```
YYYY-MM-DDTHH:MM:SS.ffffff+00:00
```

- UTC timezone only
- Microseconds (6 digits after the decimal point)
- Explicit `+00:00` UTC offset suffix

Example: `2026-04-11T08:30:00.123456+00:00`

Python strftime string: `%Y-%m-%dT%H:%M:%S.%f+00:00`

Swift DateFormatter pattern: `yyyy-MM-dd'T'HH:mm:ss.SSSSSS'+00:00'` with `locale = Locale(identifier: "en_US_POSIX")` and `timeZone = TimeZone(abbreviation: "UTC")`.

### Read format (lenient)

When reading timestamps from SQLite, implementations **SHOULD** accept:
- With or without microseconds
- With or without the `+00:00` suffix

This tolerance handles timestamps written by older or partial implementations. Always parse as UTC regardless of suffix presence.

---

## Save strategy

The `save()` operation uses a delete-and-reinsert strategy for turns:

```sql
UPDATE sessions SET updated_at = ?, metadata = ? WHERE id = ?;
DELETE FROM turns WHERE session_id = ?;
INSERT INTO turns (session_id, role, content, timestamp) VALUES (?, ?, ?, ?);
-- (one INSERT per turn)
```

Turns are append-only in practice, so delete-reinsert is safe and keeps the implementation simple. The `id` autoincrement column maintains insertion order; turns are always read `ORDER BY id ASC`.

---

## Session ID semantics

- When `continue_existing=true` and `create_if_missing=true`, the session is created using the **caller-provided ID** exactly. This makes `create_if_missing` idempotent: calling with the same ID twice creates once, then continues.
- When `continue_existing=false`, a new session is created with a fresh UUID (or equivalent random ID). The caller's `id` field is not used for storage in this case.
- The `metadata` field defaults to `{}` and is stored as a JSON string.
