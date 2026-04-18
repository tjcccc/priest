# Spec Changelog

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
