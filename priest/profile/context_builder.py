from __future__ import annotations

from priest.profile.model import Profile
from priest.schema.request import OutputSpec
from priest.session.model import Session


def build_messages(
    profile: Profile,
    session: Session | None,
    prompt: str,
    extra_context: list[str],
    output_spec: OutputSpec,
) -> list[dict]:
    """Assemble the ordered message list for a provider.

    Returns a list of dicts with 'role' and 'content' keys following the
    OpenAI messages convention. Each provider adapter is responsible for
    translating this format if its API differs.

    Context priority (system prompt sections, highest to lowest):
    1. profile.rules (RULES.md)
    2. profile.identity (PROFILE.md)
    3. profile.custom (CUSTOM.md)
    4. profile.memories
    5. extra_context strings (appended to user turn)
    6. session history turns
    7. current prompt
    """
    system_parts: list[str] = []

    if profile.rules:
        system_parts.append(profile.rules)

    if profile.identity:
        system_parts.append(profile.identity)

    if profile.custom:
        system_parts.append(profile.custom)

    for memory in profile.memories:
        if memory:
            system_parts.append(memory)

    if output_spec.mode == "json" and output_spec.strict_json:
        system_parts.append(
            "Respond only with valid JSON. No prose, no markdown code fences."
        )

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
