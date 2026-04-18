# priest protocol spec

This directory defines the **priest protocol** — a language-agnostic specification for AI orchestration libraries.

Any language SDK that implements this spec correctly is a conforming `priest` implementation. Sessions and profiles created by one implementation can be used by another.

---

## What the spec covers

| Document | Contents |
|----------|----------|
| `schemas/config.schema.json` | `PriestConfig` — provider, model, timeout, options |
| `schemas/request.schema.json` | `PriestRequest`, `SessionRef`, `OutputSpec` |
| `schemas/response.schema.json` | `PriestResponse`, `ExecutionInfo`, `UsageInfo`, `SessionInfo`, `PriestErrorModel` |
| `schemas/session.schema.json` | `Session`, `Turn` — internal persistence model |
| `schemas/profile.schema.json` | `Profile` — loaded profile representation |
| `behavior/context-assembly.md` | Exact algorithm + canonical string constants for building the message list |
| `behavior/session-lifecycle.md` | Session decision tree, SQLite schema, timestamp format, save strategy |
| `behavior/profile-loading.md` | Filesystem layout, fallback algorithm, built-in default profile content |
| `behavior/providers.md` | Per-provider wire format translation (Ollama, OpenAI-compat, Anthropic) |
| `behavior/error-codes.md` | All error codes, trigger conditions, exception vs. response error distinction |
| `behavior/streaming.md` | `stream()` contract vs. `run()`, default fallback implementation |

---

## Reference implementation

The Python `priest-core` package is the reference implementation. When spec and code differ, the spec takes precedence for the defined behavior, but any discrepancy should be treated as a bug and reported.

- Python reference: `priest/` (this repo)
- PyPI: `priest-core`

---

## Spec versioning

The spec uses [Semantic Versioning](https://semver.org/).

- **Patch** (`1.0.x`): clarifications, typo fixes, no behavioral change
- **Minor** (`1.x.0`): new optional fields, new provider support, backward-compatible additions
- **Major** (`x.0.0`): breaking changes to schemas, algorithm, or string constants

Current spec version: **2.0.0**

Each SDK implementation declares which spec version it targets. See `CHANGELOG.md` for history.

---

## Implementing against this spec

1. Read the JSON schemas for type definitions and field constraints
2. Read all `behavior/` documents — especially `context-assembly.md` (the canonical string constants are critical for correctness)
3. Implement the `SessionStore` protocol using the exact SQLite DDL and timestamp format from `session-lifecycle.md` if cross-implementation session portability is required
4. Use the built-in default profile content from `profile-loading.md` verbatim
5. Verify your implementation against the conformance checklist (coming in a future spec version)

---

## High-risk sync points

These are the spec elements most likely to cause subtle correctness bugs if not reproduced exactly:

- **Format instruction strings** (`context-assembly.md`) — must match character-for-character
- **Memory block header and separators** (`context-assembly.md`) — `"\n\n"` vs `"\n"` matters
- **ISO timestamp format** (`session-lifecycle.md`) — cross-implementation session reads depend on this
- **SQLite DDL** (`session-lifecycle.md`) — column names and types must match for portability
- **Built-in default profile content** (`profile-loading.md`) — any test that falls back to the built-in default will fail if content differs
