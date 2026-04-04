from __future__ import annotations

from priest.profile.model import Profile
from priest.schema.request import OutputSpec
from priest.session.model import Session

_FORMAT_INSTRUCTIONS: dict[str, str] = {
    "json": "Respond only with valid JSON. No prose, no markdown code fences.",
    "xml": "Respond only with valid XML. No prose, no markdown code fences.",
    "code": "Respond only with code. No prose, no markdown code fences around it.",
}


def build_messages(
    profile: Profile,
    session: Session | None,
    prompt: str,
    system_context: list[str],
    extra_context: list[str],
    output_spec: OutputSpec,
) -> list[dict]:
    """Assemble the ordered message list for a provider.

    Returns a list of dicts with 'role' and 'content' keys following the
    OpenAI messages convention. Each provider adapter is responsible for
    translating this format if its API differs.

    Context priority (system prompt sections, highest to lowest):
    1. system_context  — app-layer policy (date, environment, guardrails)
    2. profile.rules   — RULES.md
    3. profile.identity — PROFILE.md
    4. profile.custom  — CUSTOM.md
    5. profile.memories
    6. output format instruction (if format != "text")
    7. session history turns
    8. current prompt (+ extra_context appended to user turn)
    """
    system_parts: list[str] = []

    for ctx in system_context:
        if ctx:
            system_parts.append(ctx)

    if profile.rules:
        system_parts.append(profile.rules)

    if profile.identity:
        system_parts.append(profile.identity)

    if profile.custom:
        system_parts.append(profile.custom)

    non_empty_memories = [m for m in profile.memories if m and m.strip()]
    if non_empty_memories:
        memory_block = "\n".join(m.strip() for m in non_empty_memories)
        system_parts.append(f"## Loaded Memories\n\n{memory_block}")

    if output_spec.prompt_format:
        instruction = _FORMAT_INSTRUCTIONS.get(output_spec.prompt_format)
        if instruction:
            system_parts.append(instruction)

    messages: list[dict] = []

    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})

    if session is not None:
        for turn in session.turns:
            messages.append({"role": turn.role, "content": turn.content})

    user_parts = [prompt]
    for ctx in extra_context:
        if ctx:
            user_parts.append(ctx)

    messages.append({"role": "user", "content": "\n\n".join(user_parts)})

    return messages
