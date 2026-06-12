# DEVLOG

## TODO

- **Image support in `PriestRequest`:** Add an `images: list[str]` field (base64 strings or URLs) to `PriestRequest`. Provider adapters that support multimodal input (Anthropic, OpenAI-compat, Ollama vision models) should forward the images alongside the prompt. Needed by `priests` service to expose image upload via `/v1/run` and `/v1/chat`.

---

## 2026-06-12 — v2.4.0 — tool calling, structured streaming (spec 2.4.0 sync)

Syncs the spec 2.4.0 features first implemented in priest-typescript.

- **Tool calling (caller executes):** `PriestRequest.tools` / `tool_choice` / `tool_exchange`, `PriestResponse.tool_calls`, `finished_reason: "tool_calls"`. Wire mappings for all three providers (OpenAI tools/tool_calls with JSON-string arguments, Anthropic tool_use/tool_result with merged user messages, Ollama tools with synthesized `call_N` ids and `tool_name` results). Tool exchange turns are never persisted in sessions — schema interop with pre-2.4 SDKs preserved.
- **`run_with_tools()` loop helper** (`priest/tool_loop.py`): generic call → execute → re-call loop with caller executor, optional `on_tool_call` approval hook, iteration cap, and exchange trace.
- **`PriestEngine.stream_events()`:** structured streaming (`text_delta`, `tool_call_start/delta/end`, `usage`, `done` with full `PriestResponse`); adapters without native event streaming are wrapped; `stream()` reimplemented as a filter over it.
- **Cancellation:** Python maps the spec's cancellation concept to native asyncio task cancellation; `ErrorCode.REQUEST_ABORTED` added for parity.
- `AdapterCallOptions` / `AdapterStreamEvent` added to the adapter base; `AdapterResult.tool_calls` added.
- Tests: 86 (9 new in `tests/test_tool_calling.py`).

---

## 2026-05-08 — v2.3.0 — optional profile memory loading

- Added `FilesystemProfileLoader(..., include_memories=False)` so host applications can load profile identity/rules/custom docs without also injecting `memories/`
- Cache invalidation now respects the opt-out: profile memory files are only tracked when memory loading is enabled
- This keeps `priest` generic while allowing apps such as `priests` to own product-level memory semantics and pass selected memory through `PriestRequest.memory`

---

## 2026-04-25 — v2.2.0 — structured output (json_schema)

Added `json_schema`, `json_schema_name`, and `json_schema_strict` to `OutputSpec` for per-provider JSON Schema wiring.

- **OpenAI-compat:** `response_format: {"type": "json_schema", "json_schema": {name, schema, strict}}`. Takes precedence over `provider_format="json"` when set.
- **Ollama (v0.5+):** `format: <schema_dict>` passed directly. Takes precedence over `provider_format="json"`.
- **Anthropic:** no native JSON Schema support — the adapter injects a structured instruction block into the assembled system message for both `complete` and `stream`.
- `json_schema_strict` defaults to `False`; strict mode requires every property in `required` and `additionalProperties: false`, which most user schemas won't satisfy.
- 13 tests: `OutputSpec` defaults, round-trip, all three adapter `complete` paths, all three adapter `stream` paths, and `_schema_instruction` content.

---

## 2026-04-18 — v2.0.0 — context API redesign

Breaking redesign of `PriestRequest` context fields plus library-level optimization for profile and memory.

**Why:** `priests` (and future callers) need the library to do basic trimming and deduplication so the app layer doesn't have to. At the same time, the old three-way split (`system_context` / `memory_context` / `extra_context`) conflated "raw passthrough" with "library-managed" content and hid the user-turn-vs-system-prompt distinction behind a name that didn't suggest it.

**New shape:**
- `PriestRequest.context` — raw system-level strings, untouched by the library. Callers who want full control over the system prompt put everything here.
- `PriestRequest.memory` — dynamic memory entries. The library dedupes (by stripped content, against self and profile.memories) and, when `config.max_system_chars` is set, trims from the tail.
- `PriestRequest.user_context` — appended to the user turn (RAG chunks, tool outputs, search results). Follows the community convention of keeping ephemeral per-turn content with the user message rather than the persistent system prompt (OpenAI, Anthropic, LangChain, LlamaIndex all do this).

**New config:**
- `PriestConfig.max_system_chars: int | None = None`. Default `None` — no silent trimming, matching the principle of least astonishment followed by `tiktoken`, the OpenAI/Anthropic SDKs, and `transformers`. Callers opt in when they need safety; no single default is right across models (Claude 200k, Gemini 1M, Llama 128k, older 8k).

**Trim strategy:** when the budget is exceeded, drop dynamic memory entries from the tail first, then profile memories from the tail. `context`, rules, identity, custom, and the format instruction are never trimmed (they're structural). If still over budget after all memory is gone, log a warning and continue.

**Profile caching:** `FilesystemProfileLoader` now caches Profile instances per-loader keyed on (max mtime, file count) across `PROFILE.md`, `RULES.md`, `CUSTOM.md`, `profile.toml`, and `memories/*`. Any edit, add, or remove invalidates. Cold-reload cost drops to a single `stat()` loop per run.

**Protocol spec bumped to 2.0.0.** `spec/schemas/request.schema.json`, `spec/schemas/config.schema.json`, `spec/behavior/context-assembly.md` updated. Memory dedup and trim algorithm documented as canonical behavior.

**Tests:** 58 unit tests passing (was 46). New coverage: memory dedup (self + cross-source), tail-trim (memory and profile), trim priority order (dynamic fully drained before profile), budget no-op when None, profile cache hit, cache invalidation on mtime change, cache invalidation on new memory file.

**priests CLI:** field renames only (`system_context` → `context`, `extra_context` → `user_context`). Memory block still goes via `context` because priests' memory formatting is domain-specific; the raw `memory` field is available when callers want library-level dedup/trim.

---

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
