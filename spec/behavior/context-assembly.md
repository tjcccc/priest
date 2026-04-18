# Context Assembly

This document defines the exact algorithm for building the `messages` list passed to a provider adapter. Implementations **MUST** reproduce this algorithm and use the canonical string constants verbatim.

Reference implementation: `priest/profile/context_builder.py`

---

## Output format

The engine always produces a list of message objects following the OpenAI messages convention:

```json
[
  { "role": "system",    "content": "..." },
  { "role": "user",      "content": "..." },
  { "role": "assistant", "content": "..." },
  ...
]
```

Each provider adapter is responsible for translating this format to its own wire format if it differs (e.g. Anthropic requires system as a top-level field — see `behavior/providers.md`).

---

## Inputs

- `request.context`       — list[str], raw, never trimmed or deduped
- `profile.rules`         — str, raw, never trimmed
- `profile.identity`      — str, raw, never trimmed
- `profile.custom`        — str, raw, never trimmed
- `profile.memories`      — list[str], normalized (strip + drop empties); subject to dedup and tail-trim
- `request.memory`        — list[str], normalized and deduped; subject to tail-trim
- `request.user_context`  — list[str], appended to the user turn
- `request.prompt`        — str
- `request.output.prompt_format` — optional str
- `request.config.max_system_chars` — optional int (no trimming when null)

---

## Algorithm

### Step 1 — Normalize profile memories

```
profile_memories = [m.strip() for m in profile.memories if m and m.strip()]
```

### Step 2 — Deduplicate dynamic memory

```
seen = set(profile_memories)
dynamic_memory = []
for each entry in request.memory:
    stripped = entry.strip()
    if stripped is empty: skip
    if stripped in seen: skip
    seen.add(stripped)
    dynamic_memory.append(stripped)
```

A dynamic memory entry is dropped if its stripped content matches any already-seen entry — either another dynamic entry earlier in the list, or any entry in `profile_memories`.

### Step 3 — Trim to budget (only when `max_system_chars` is set)

Given the assembly function `assemble(dynamic, profile_mem)` (defined in Step 4), if `len(assemble(dynamic_memory, profile_memories)) > max_system_chars`:

1. Drop entries from the tail of `dynamic_memory` until `len(assemble(dynamic_memory, profile_memories)) ≤ max_system_chars` or `dynamic_memory` is empty.
2. If the budget is still exceeded, drop entries from the tail of `profile_memories` until the budget is met or `profile_memories` is empty.
3. If the budget is still exceeded, log a warning and continue — no further trimming is performed. `context`, rules, identity, custom, and the format instruction are **never** trimmed.

### Step 4 — Assemble system content

```
system_parts = []

for each string ctx in request.context (in order):
    if ctx is non-empty:
        append ctx to system_parts

if profile.rules is non-empty:
    append profile.rules to system_parts

if profile.identity is non-empty:
    append profile.identity to system_parts

if profile.custom is non-empty:
    append profile.custom to system_parts

if profile_memories is non-empty:
    block = "## Loaded Memories\n\n" + join(profile_memories with "\n")
    append block to system_parts

if dynamic_memory is non-empty:
    block = "## Memory\n\n" + join(dynamic_memory with "\n")
    append block to system_parts

if request.output.prompt_format is set (not null):
    instruction = FORMAT_INSTRUCTIONS[request.output.prompt_format]
    append instruction to system_parts

system_content = join(system_parts with "\n\n")
```

### Step 5 — Build message list

```
messages = []

if system_content is non-empty:
    messages.append({ role: "system", content: system_content })

if session is not null:
    for each turn in session.turns (in order):
        messages.append({ role: turn.role, content: turn.content })

user_parts = [request.prompt]
for each string ctx in request.user_context (in order):
    if ctx is non-empty:
        user_parts.append(ctx)

user_content = join(user_parts with "\n\n")
messages.append({ role: "user", content: user_content })

return messages
```

**If `system_content` is empty, no system message is added.** The first message in the list will be the first session turn (if any) or the user message.

---

## Canonical string constants

These strings **MUST** be reproduced exactly. Any variation (extra space, different punctuation, different capitalization) is a spec violation.

### Format instruction strings

| `prompt_format` value | Instruction string |
|-----------------------|--------------------|
| `"json"` | `Respond only with valid JSON. No prose, no markdown code fences.` |
| `"xml"` | `Respond only with valid XML. No prose, no markdown code fences.` |
| `"code"` | `Respond only with code. No prose, no markdown code fences around it.` |

### Memory block headers

Static memories (loaded from `profile.memories`) are wrapped in:

```
## Loaded Memories\n\n
```

Dynamic memory entries (from `request.memory`) are wrapped in:

```
## Memory\n\n
```

Each is two characters (`#`, `#`), a space, the heading text, then two newlines before the content.

### Separators

| Location | Separator |
|----------|-----------|
| Between system parts | `"\n\n"` (two newlines) |
| Between profile memory entries (within `## Loaded Memories` block) | `"\n"` (one newline, each entry stripped) |
| Between dynamic memory entries (within `## Memory` block) | `"\n"` (one newline, each entry stripped) |
| Between user parts (prompt + user_context) | `"\n\n"` (two newlines) |

---

## Context priority order

From highest to lowest priority in the system prompt:

1. `request.context`   — raw, untouched (injected first, visible at top)
2. `profile.rules`     — RULES.md
3. `profile.identity`  — PROFILE.md
4. `profile.custom`    — CUSTOM.md
5. `profile.memories`  — wrapped in `## Loaded Memories`
6. `request.memory`    — wrapped in `## Memory`
7. Format instruction (if `output.prompt_format` is set)

Then, in the message list:

8. Session history turns (in chronological order)
9. Current user prompt (+ `user_context` appended, `\n\n`-joined)

---

## Examples

### Minimal request (no profile content, no session, no extras)

If a completely empty profile is loaded (all fields empty, no memories) and `context`, `memory`, `user_context` are all empty, `system_parts` is empty and the output is:

```json
[{ "role": "user", "content": "Hello." }]
```

### Request with `context`, dynamic memory, and format instruction

```
context = ["Today is 2026-04-11.", "App: MyGame"]
memory  = ["User is currently on the mobile app."]
profile.rules = "Be concise."
profile.identity = "You are a game NPC."
profile.memories = ["User's name is Atlas."]
output.prompt_format = "json"
```

Resulting system message content:
```
Today is 2026-04-11.

App: MyGame

Be concise.

You are a game NPC.

## Loaded Memories

User's name is Atlas.

## Memory

User is currently on the mobile app.

Respond only with valid JSON. No prose, no markdown code fences.
```

### Request with memory dedup and trimming

```
config.max_system_chars = 200
profile.memories = ["Fact A."]
memory = ["Fact A.", "Fact B.", "Fact C." * 100, "Fact D."]
```

After dedup and trim:
- `"Fact A."` from `memory` is dropped (matches profile.memories)
- If the assembled prompt with `["Fact B.", "Fact C." × 100, "Fact D."]` exceeds 200 chars, entries are dropped tail-first: `"Fact D."`, then `"Fact C." × 100`, until the budget is met.
