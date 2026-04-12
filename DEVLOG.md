# DEVLOG

## 2026-04-12 — v1.0.0 release

First stable release. Version bumped to 1.0.0.

## 2026-04-11 — v1.0.0 pre-release fixes

Pre-release hardening pass. All issues are resolved; version tag will follow once LICENSE is decided.

**Bug fixes:**
- `OpenAICompatProvider.stream()` was nested inside `_call_sync()` (dead code since it was introduced). Moved to class level — streaming now actually works for OpenAI-compatible providers.
- `asyncio.get_event_loop()` → `asyncio.get_running_loop()` in both `AnthropicProvider` and `OpenAICompatProvider` stream methods. The old call is deprecated in Python 3.10+ and breaks in 3.14.

**New:**
- `ProviderRateLimitedError` concrete exception class added — the `PROVIDER_RATE_LIMITED` error code existed but had no corresponding exception type.
- Streaming test suite added (`tests/test_streaming.py`): 6 tests covering chunk delivery, session persistence after stream, session continuation, unknown-provider error, and no-session-store path.
- `MockAdapter.stream()` implemented — yields text one word at a time for unit testing.
- `py.typed` marker added (PEP 561) — signals typed package to downstream type checkers.

**Public API expanded:**
- `__init__.py` now exports all response sub-types (`ExecutionInfo`, `UsageInfo`, `SessionInfo`), all exception types and `ErrorCode`, and adapter base types (`ProviderAdapter`, `AdapterResult`) — everything a downstream library author or custom-provider implementer needs.

**Packaging:**
- `pyproject.toml`: added `readme`, `classifiers`, `[project.urls]`, and registered `integration` pytest marker (eliminates warning on unit test runs).

**Tests:** 35 unit tests passing. 4 integration tests (Ollama) untouched.

---

## 2026-04-01 — Core semantics cleanup

Post-review fixes addressing session ID coherence, output format design, and cost_limit noise.

**Session ID semantics fixed:**
- `SessionStore.create()` now accepts an optional `session_id` parameter
- When `create_if_missing=True`, the caller's provided ID is honored — the session is created with exactly that ID
- This makes session initialization idempotent: same ID, same session

**Output format redesigned:**
- `OutputSpec.mode` + `strict_json` replaced with two independent fields:
  - `provider_format`: activates provider-native structured output (e.g. Ollama's `format` field). Currently `"json"` only.
  - `prompt_format`: injects a natural-language instruction into the system prompt. Supports `"json"`, `"xml"`, `"code"`.
- Both are `None` by default (no-op)
- Core **never parses** the response. `response.text` is always the raw string — parsing is the app layer's responsibility
- `PriestResponse.json_payload` removed — it was core doing app-layer work

**Cost limit:** removed noisy debug log that fired on every run. Advisory comment on the field is sufficient.

---

## 2026-03-31 — Milestone 1 complete

Initial implementation of the `priest` core library. All Milestone 1 deliverables are in place and passing.

**Package structure:** `priest/errors.py`, `priest/engine.py`, `priest/schema/`, `priest/profile/`, `priest/session/`, `priest/providers/`

**Key decisions made:**

- Schema: nested sub-objects — `PriestConfig`, `PriestRequest`, `PriestResponse` with `ok` property. `provider` and `model` stay as separate fields inside `PriestConfig`.
- Session storage: Abstract `SessionStore` ABC + `SqliteSessionStore` default + `InMemorySessionStore` for tests. `aiosqlite` for async SQLite. No default path hardcoded in core.
- Profile loading: `FilesystemProfileLoader` is sync (startup-adjacent, not a hot path). Engine is handed a resolved `Profile` dataclass — it never touches the filesystem after that. Built-in fallback default profile in `default_profile.py`.
- Context order: system_context → rules → identity → custom → memories → session history → user prompt.
- Provider adapters: `OllamaProvider` via `httpx` async. `provider_options: dict` on `PriestConfig` forwards arbitrary fields into the provider payload (e.g. `{"think": False}` for Qwen3 no-thinking mode).
- `scripts/try_run.py` supports `--prompt`, `--chat`, and bare smoke-test modes.

**Tests:** 29 unit tests + 4 integration tests (Ollama), all passing.

**Verified against:** Ollama + `qwen3.5:9b` locally. With `think: False`, latency ~1s for short prompts.

### Out of scope for Milestone 1 (deferred to M2)

- `openai_provider.py`
- `profile.toml` metadata parsing (stub dict only)
- Token estimation utilities
- Cost enforcement (advisory field exists, enforcement deferred)
- Streaming responses
- Memory selection/ranking (all memories loaded in filename order)
