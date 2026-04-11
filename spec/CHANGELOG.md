# Spec Changelog

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
