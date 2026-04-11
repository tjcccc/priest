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

## Algorithm

### Step 1 — Build system parts list

```
system_parts = []

for each string ctx in request.system_context (in order):
    if ctx is non-empty:
        append ctx to system_parts

if profile.rules is non-empty:
    append profile.rules to system_parts

if profile.identity is non-empty:
    append profile.identity to system_parts

if profile.custom is non-empty:
    append profile.custom to system_parts

non_empty_memories = [m for m in profile.memories if m is non-empty after strip()]
if non_empty_memories is non-empty:
    memory_block = join(non_empty_memories with "\n", each stripped)
    append ("## Loaded Memories\n\n" + memory_block) to system_parts

if request.output.prompt_format is set (not null):
    instruction = FORMAT_INSTRUCTIONS[request.output.prompt_format]
    append instruction to system_parts
```

### Step 2 — Build message list

```
messages = []

if system_parts is non-empty:
    system_content = join(system_parts with "\n\n")
    messages.append({ role: "system", content: system_content })

if session is not null:
    for each turn in session.turns (in order):
        messages.append({ role: turn.role, content: turn.content })

user_parts = [request.prompt]
for each string ctx in request.extra_context (in order):
    if ctx is non-empty:
        user_parts.append(ctx)

user_content = join(user_parts with "\n\n")
messages.append({ role: "user", content: user_content })

return messages
```

**If `system_parts` is empty, no system message is added.** The first message in the list will be the first session turn (if any) or the user message.

---

## Canonical string constants

These strings **MUST** be reproduced exactly. Any variation (extra space, different punctuation, different capitalization) is a spec violation.

### Format instruction strings

| `prompt_format` value | Instruction string |
|-----------------------|--------------------|
| `"json"` | `Respond only with valid JSON. No prose, no markdown code fences.` |
| `"xml"` | `Respond only with valid XML. No prose, no markdown code fences.` |
| `"code"` | `Respond only with code. No prose, no markdown code fences around it.` |

### Memory block header

The memories section header is:

```
## Loaded Memories\n\n
```

Two characters: `#`, `#`, space, then `Loaded Memories`, then two newlines before the memory content.

### Separators

| Location | Separator |
|----------|-----------|
| Between system parts | `"\n\n"` (two newlines) |
| Between memory file contents | `"\n"` (one newline, each memory stripped) |
| Between user parts (prompt + extra_context) | `"\n\n"` (two newlines) |

---

## Context priority order

From highest to lowest priority in the system prompt:

1. `request.system_context` — app-layer policy (injected first, visible at top)
2. `profile.rules` — RULES.md
3. `profile.identity` — PROFILE.md
4. `profile.custom` — CUSTOM.md
5. `profile.memories` — memory files, wrapped in the `## Loaded Memories` block
6. Format instruction (if `output.prompt_format` is set)

Then, in the message list:

7. Session history turns (in chronological order)
8. Current user prompt (+ extra_context appended)

---

## Examples

### Minimal request (no profile content, no session, no extras)

If the built-in default profile is used with empty rules and identity, `system_parts` will have content. If a completely empty profile is loaded (all fields empty, no memories), `system_parts` will be empty and the output will be:

```json
[{ "role": "user", "content": "Hello." }]
```

### Request with system_context and format instruction

```
system_context = ["Today is 2026-04-11.", "App: MyGame"]
profile.rules = "Be concise."
profile.identity = "You are a game NPC."
output.prompt_format = "json"
```

Resulting system message content:
```
Today is 2026-04-11.

App: MyGame

Be concise.

You are a game NPC.

Respond only with valid JSON. No prose, no markdown code fences.
```
