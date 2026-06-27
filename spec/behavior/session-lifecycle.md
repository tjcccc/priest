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

**Tool-calls rule (spec 2.4.0):** when the response carries tool calls (`finished_reason == "tool_calls"`), **nothing is appended or saved** — the turn is still in progress. `response.session` is still populated so the caller can observe session identity. Tool exchange turns themselves are never persisted: the `turns` table only ever stores `user` and `assistant` roles, and only the original `request.prompt` plus the final assistant text of a tool loop are written. See `tool-calling.md`. (Images on the user turn are likewise not persisted — only the text prompt is stored.)

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

---

## Conversation compaction (spec 2.5.0)

Long sessions otherwise replay their entire turn history on every call, so input cost grows linearly per turn and quadratically over a session. Compaction folds the older turns into a running **summary** and replays only a recent tail, bounding the replayed history. It is **non-destructive**: the raw `turns` rows are never deleted — only the *replayed view* (see `context-assembly.md`) shrinks. Compaction is **off by default** and enabled only when `config.max_context_tokens` is set.

### Persistence contract (`__compaction` metadata)

Compaction state is stored **inside the existing `metadata` JSON** under the reserved key `__compaction` — no schema change, so a session written by a compaction-aware SDK is still readable by a pre-2.5 SDK (which ignores the key). The object uses these **exact camelCase field names** (a cross-SDK interop contract — all SDKs MUST serialize/read these keys verbatim, regardless of the host language's idiomatic casing):

| Field | Type | Meaning |
|---|---|---|
| `summary` | string | Running synopsis covering `turns[0 .. summarizedThrough)`. |
| `summarizedThrough` | int | Number of leading turns folded into `summary` (an index into `turns`). |
| `lastInputTokens` | int | Provider-reported input tokens of the most recent **measured** (clean chat) turn — the compaction trigger signal. |
| `updatedAt` | string | ISO-8601 timestamp of the last compaction-state update. |

All fields are optional; an absent `__compaction` key (or empty object) means "never compacted." Example `metadata`:

```json
{ "__compaction": { "summary": "User is building …", "summarizedThrough": 4, "lastInputTokens": 1850, "updatedAt": "2026-06-25T09:00:00.000000+00:00" } }
```

### Trigger and timing

- The budget is `config.max_context_tokens` (unset or `<= 0` ⇒ compaction disabled). The threshold is **80%** of the budget (`COMPACTION_TRIGGER_RATIO = 0.8`).
- The trigger reads the **previous** chat turn's reported input usage (`lastInputTokens`); there is no tokenizer dependency. The crossing turn overshoots by one, then compaction applies **before** the next turn is built.
- `record_chat_usage` writes `lastInputTokens` **after** a call, but **only for clean chat turns** — a turn that **replays a tool exchange** (`request.tool_exchange` non-empty) is skipped, because its input is inflated by intra-run tool context (web results, agent iterations) rather than the clean persisted session. Merely *offering* tools (tools available but not invoked, so no tool exchange replayed) still records.

### Folding (`plan` + `compact`)

`compaction_keep_turns` (default **6**) most-recent turns are kept verbatim; everything after `summarizedThrough` and before that kept tail is folded this round. A round:

1. `tail_start = max(0, len(turns) - max(0, keep_turns))`. If `tail_start <= summarized_through`, there is nothing new to fold → no-op (makes repeated/recursive calls safe).
2. Otherwise summarize `turns[summarized_through .. tail_start)` via a **provider `complete()` call** using the compaction system prompt, merging any existing `summary`. The summary call caps output at `SUMMARY_MAX_OUTPUT_TOKENS = 1024` (unless `config.max_output_tokens` is already set).
3. On a non-empty result, set `summary` and advance `summarizedThrough = tail_start`, then `save()` the session. An empty summary result is a no-op.

Compaction is **recursive**: a later round folds only the newly-aged turns into the existing summary (it never re-folds `turns[0 .. summarizedThrough)`).

### Entry points

- **Automatic:** `run()` / `stream()` call `maybe_compact(session, config)` **before building the request messages**; it compacts when `should_compact(lastInputTokens, max_context_tokens)` is true.
- **Manual:** `engine.compact_session(session_id, config, options?)` folds on demand (ignoring the budget) and returns `{ compacted: bool, summarized_through: int }`. Raises `SESSION_NOT_FOUND` for an unknown id. Returns `{ compacted: false }` when there is no session store.
