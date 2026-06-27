# Spec Changelog

## 2.6.1 — 2026-06-27

Provider clarification, additive — no request becomes invalid and sessions remain interoperable.

### Changed
- **OpenAI-compatible streaming usage** (`behavior/providers.md`): streaming requests must also send `stream_options: {include_usage: true}` so the gateway emits a final usage chunk. Without it, OpenAI-compatible gateways (e.g. Alibaba DashScope/Bailian) report streaming usage only for models that volunteer it, so cost/context goes missing for third-party models (Qwen volunteered usage; `deepseek-v4-flash` did not). The flag applies to streaming **only** (non-streaming `complete()` is unchanged) and is overridable via `config.provider_options`.

### Reference implementation
- TypeScript `@priest-ai/core` v2.6.1; Python `priest-core` v2.6.1. priest-dotnet / priest-rs / PriestSwift v2.6.1.

---

## 2.6.0 — 2026-06-25

Session turn window. Additive — unset preserves prior behavior; sessions remain interoperable.

### Added
- **`config.session_context_turns`** (`behavior/context-assembly.md`): a hard cap on how many recent session turns are replayed into a request. When set, only the last N turns (after any compaction summary) reach the model; older turns stay on disk but are not sent. `0` replays none (summary only); unset replays all (default, fully backward compatible). Independent of the compaction budget (`max_context_tokens`), which remains the usage-triggered safety net.
- **Step-5 windowing rule:** the session replay starts at `max(summarized_through, len(turns) - N)` — a window never un-hides turns already folded into the summary. An **odd-sized window snaps down to a user turn** (floored by `summarized_through`) so the replay never opens on an orphan assistant reply, which strict OpenAI-compatible backends (e.g. DashScope) reject.

### Reference implementation
- TypeScript `@priest-ai/core` v2.6.0. Other SDKs synced at v2.6.1.

---

## 2.5.0 — 2026-06-25

Conversation compaction and cached-token visibility. Additive — both off by default; the SQLite schema and cross-SDK session interop are unchanged (compaction state lives in the existing `metadata` column).

### Added
- **Cached input tokens** (`behavior/providers.md`, `behavior/streaming.md`): `AdapterResult.cached_input_tokens` / `UsageInfo.cached_input_tokens` and the `usage` stream event now carry the prompt-cache hit count. Parsed from OpenAI-compatible `usage.prompt_tokens_details.cached_tokens` and Anthropic `usage.cache_read_input_tokens` (both `complete` and streaming). Lets hosts observe prefix-cache behavior instead of only gross input. Omitted (null) when the provider does not report it.
- **Conversation compaction** (`behavior/session-lifecycle.md`, `behavior/context-assembly.md`): sessions can be bounded instead of replaying full history forever (input cost is otherwise linear per turn, quadratic per session). New `config.max_context_tokens` (enables compaction; unset = off) and `config.compaction_keep_turns` (default 6). When a chat turn's reported input usage crosses **80%** of the budget, the engine folds older turns into a running summary via a provider summarization call and replays only `summary + recent tail`. Non-destructive: raw turns stay in the store; only the replayed view shrinks. The summary and trigger signal live in session `metadata` under the `__compaction` key (camelCase fields — see `session-lifecycle.md`), so the schema and pre-2.5 interop are unchanged.
- **`engine.compact_session(id, config, options?)`**: manual `/compact` entry point. Folds older turns on demand and reports `{compacted, summarized_through}`; raises `SESSION_NOT_FOUND` for an unknown id.
- **Compaction semantics** (`behavior/session-lifecycle.md`): the trigger is measured on the **previous** chat turn's reported input (overshoots the budget by one turn, then compacts before the next). Turns that **replay a tool exchange** are skipped for the trigger (their input reflects intra-run tool context, not the clean session); merely *offering* tools still records. `max_context_tokens` is independent of `max_system_chars` (system-prompt char trimming).

### Reference implementation
- TypeScript `@priest-ai/core` v2.5.0. Other SDKs synced at v2.6.1.

---

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
