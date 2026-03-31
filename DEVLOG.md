# DEVLOG

## 2026-03-31 — Milestone 1 complete

### What was built

Initial implementation of the `priest` core library. All Milestone 1 deliverables are in place and passing.

**Package structure:** `priest/errors.py`, `priest/engine.py`, `priest/schema/`, `priest/profile/`, `priest/session/`, `priest/providers/`

**Key decisions made:**

- Schema: Option B (nested sub-objects) — `PriestConfig`, `PriestRequest`, `PriestResponse` with `ok` property. `provider` and `model` stay as separate fields inside `PriestConfig`.
- Session storage: Abstract `SessionStore` ABC + `SqliteSessionStore` default + `InMemorySessionStore` for tests. `aiosqlite` for async SQLite. No default path hardcoded in core.
- Profile loading: `FilesystemProfileLoader` is sync (startup-adjacent, not a hot path). Engine is handed a resolved `Profile` dataclass — it never touches the filesystem after that.
- Context order: rules → identity → custom → memories → session history → user prompt.
- Provider adapters: `OllamaProvider` via `httpx` async. `provider_options: dict` on `PriestConfig` forwards arbitrary fields into the provider payload (e.g. `{"think": False}` for Qwen3 no-thinking mode).
- `scripts/try_run.py` supports `--prompt`, `--chat`, and bare smoke-test modes.

**Tests:** 11 unit tests, all passing. No Ollama required for unit tests.

**Verified against:** Ollama + `qwen3.5:9b` locally. With `think: False`, latency ~1s for short prompts.

### Out of scope for Milestone 1 (deferred to M2)

- `openai_provider.py`
- `profile.toml` metadata parsing (stub dict only)
- Token estimation utilities
- Cost enforcement (advisory field exists, enforcement deferred)
- Streaming responses
- Memory selection/ranking (all memories loaded in filename order)
