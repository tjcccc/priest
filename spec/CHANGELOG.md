# Spec Changelog

## 2.4.0 — 2026-06-12

Native tool calling, structured streaming, cancellation, and image parity. All changes are additive — 2.3.0 requests remain valid and sessions remain fully interoperable.

### Added
- **Tool calling** (`behavior/tool-calling.md`): `PriestRequest.tools` / `tool_choice` / `tool_exchange`; `PriestResponse.tool_calls`; `finished_reason: "tool_calls"`; `ToolDefinition` / `ToolCall` / `ToolExchangeTurn` schema blocks. The caller executes tools; the library transports. Tool exchange turns are appended after the user message and are **never persisted in sessions** (no schema change — `turns` still stores only user/assistant roles).
- **`stream_events()`** (`behavior/streaming.md`): typed event stream (`text_delta`, `tool_call_start/delta/end`, `usage`, `done` with full `PriestResponse`); engine-level fallback wraps plain adapter `stream()`; error semantics match `run()`.
- **Cancellation** (`behavior/streaming.md`): optional caller cancellation signal on `run()`/`stream()`/`stream_events()`; new `REQUEST_ABORTED` error code, distinct from `PROVIDER_TIMEOUT`.
- **`run_with_tools` convenience loop** (`behavior/tool-calling.md`): recommended caller-executes loop with approval hook, iteration cap, and exchange trace.
- Per-provider tool and image wire mappings in `behavior/providers.md`, including Ollama call-id synthesis (`call_N`) and Anthropic tool_result/user-message merging.
- `IMAGE_LOAD_ERROR` added to the error table (existed in the Python reference; previously missing from the spec).

### Reference implementation
- TypeScript `@priest-ai/core` v2.4.0; Python `priest-core` v2.4.0

---

## 2.3.0 — 2026-05-08

### Added
- `FilesystemProfileLoader(..., include_memories=False)` lets host applications disable automatic loading of profile `memories/*.md` / `*.txt` files while still using filesystem profiles.

### Reference implementation
- Python `priest-core` v2.3.0

---

## 2.0.0 — 2026-04-18

**Breaking:** the `PriestRequest` context fields are collapsed and renamed. This is a breaking change to the request schema and the context-assembly algorithm.

### Changed
- `PriestRequest.system_context` + `PriestRequest.memory_context` → `PriestRequest.context` (raw, untouched) and `PriestRequest.memory` (deduped, trimmable)
- `PriestRequest.extra_context` → `PriestRequest.user_context` (same semantics: appended to the user turn)
- Context assembly now runs an explicit dedup step for `memory` (against itself and against `profile.memories`) and an optional tail-trim step (dynamic memory first, then profile memories).

### Added
- `PriestConfig.max_system_chars` (int | null): optional system prompt size budget. When set, the library trims `request.memory` and then `profile.memories` from the tail until the budget is met. `context`, rules, identity, custom, and format instructions are never trimmed. Null (default) disables trimming.
- `## Memory` heading: dynamic memory entries (from `request.memory`) are now wrapped under `## Memory\n\n`, rendered after the `## Loaded Memories` block for profile-level memories.
- `ImageInput` schema block now defined in `request.schema.json`.

### Migration
- `system_context=[...]` + `memory_context=[...]` → `context=[...]` + `memory=[...]` (or merge both into `context` if you want full control).
- `extra_context=[...]` → `user_context=[...]`.

### Reference implementation
- Python `priest-core` v2.0.0

---

## 1.0.0 — 2026-04-11

Initial spec release, extracted from the `priest-core` Python reference implementation (v0.2.1).

### Schemas defined
- `PriestConfig`, `PriestRequest`, `SessionRef`, `OutputSpec`
- `PriestResponse`, `ExecutionInfo`, `UsageInfo`, `SessionInfo`, `PriestErrorModel`
- `Session`, `Turn`
- `Profile`

### Behavior documented
- Context assembly algorithm with canonical string constants (format instructions, separators, memory block header)
- Session lifecycle decision tree, SQLite schema, timestamp format, save strategy
- Profile filesystem layout, loading algorithm, built-in default profile content
- Ollama, OpenAI-compatible, and Anthropic provider translations
- Error codes and exception vs. response-error distinction
- `stream()` vs `run()` contract

### Reference implementation
- Python `priest-core` v0.2.1
