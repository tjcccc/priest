from __future__ import annotations

import base64
import logging
from pathlib import Path

from priest.errors import ImageLoadError
from priest.profile.model import Profile
from priest.schema.request import ImageInput, OutputSpec
from priest.session.model import Session

logger = logging.getLogger(__name__)

_FORMAT_INSTRUCTIONS: dict[str, str] = {
    "json": "Respond only with valid JSON. No prose, no markdown code fences.",
    "xml": "Respond only with valid XML. No prose, no markdown code fences.",
    "code": "Respond only with code. No prose, no markdown code fences around it.",
}


def build_messages(
    profile: Profile,
    session: Session | None,
    prompt: str,
    context: list[str],
    memory: list[str],
    user_context: list[str],
    output_spec: OutputSpec,
    images: list[ImageInput] | None = None,
    max_system_chars: int | None = None,
) -> list[dict]:
    """Assemble the ordered message list for a provider.

    Returns a list of dicts with 'role' and 'content' keys following the
    OpenAI messages convention. Each provider adapter is responsible for
    translating this format if its API differs.

    Context priority (system prompt sections, highest to lowest):
    1. context          — raw app-layer system context, untouched
    2. profile.rules    — RULES.md
    3. profile.identity — PROFILE.md
    4. profile.custom   — CUSTOM.md
    5. profile.memories — static memory files (deduped, tail-trimmed if needed)
    6. memory           — dynamic memory entries (deduped vs. self and profile.memories,
                          tail-trimmed first when max_system_chars is set)
    7. output format instruction (if prompt_format is set)
    8. session history turns
    9. current prompt (+ user_context appended to user turn)

    Deduplication: within `memory`, later entries whose stripped content matches
    an earlier memory entry or any profile.memories entry are dropped.

    Trimming (only when max_system_chars is set): if the combined system prompt
    exceeds the budget, drop dynamic `memory` entries from the tail, then
    profile.memories entries from the tail. `context`, rules, identity, custom,
    and the format instruction are never trimmed. If the budget is still exceeded,
    a warning is logged and the system prompt is returned as-is.

    When images are provided, the user message content becomes a list of
    content blocks in OpenAI multimodal format (images first, text last).
    """
    profile_memories = _normalize_memories(profile.memories)
    dynamic_memory = _dedupe_memories(memory, existing=profile_memories)

    if max_system_chars is not None and max_system_chars > 0:
        dynamic_memory, profile_memories = _trim_to_budget(
            context=context,
            profile=profile,
            profile_memories=profile_memories,
            dynamic_memory=dynamic_memory,
            output_spec=output_spec,
            budget=max_system_chars,
        )

    system_content = _assemble_system(
        context=context,
        profile=profile,
        profile_memories=profile_memories,
        dynamic_memory=dynamic_memory,
        output_spec=output_spec,
    )

    messages: list[dict] = []

    if system_content:
        messages.append({"role": "system", "content": system_content})

    if session is not None:
        for turn in session.turns:
            messages.append({"role": turn.role, "content": turn.content})

    user_text_parts = [prompt]
    for ctx in user_context:
        if ctx:
            user_text_parts.append(ctx)
    user_text = "\n\n".join(user_text_parts)

    if images:
        content_blocks: list[dict] = [_image_to_block(img) for img in images]
        content_blocks.append({"type": "text", "text": user_text})
        messages.append({"role": "user", "content": content_blocks})
    else:
        messages.append({"role": "user", "content": user_text})

    return messages


def _normalize_memories(memories: list[str]) -> list[str]:
    """Strip and drop empties; preserve order."""
    return [m.strip() for m in memories if m and m.strip()]


def _dedupe_memories(dynamic: list[str], *, existing: list[str]) -> list[str]:
    """Return dynamic memory entries with duplicates dropped.

    A dynamic entry is dropped if its stripped content matches any earlier
    dynamic entry (by stripped content) or any entry in `existing`.
    """
    seen: set[str] = set(existing)
    result: list[str] = []
    for entry in dynamic:
        if not entry:
            continue
        stripped = entry.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        result.append(stripped)
    return result


def _trim_to_budget(
    *,
    context: list[str],
    profile: Profile,
    profile_memories: list[str],
    dynamic_memory: list[str],
    output_spec: OutputSpec,
    budget: int,
) -> tuple[list[str], list[str]]:
    """Trim dynamic_memory (tail first) then profile_memories (tail first) to fit budget.

    Returns the possibly-shortened (dynamic_memory, profile_memories) lists.
    """
    def _size(dyn: list[str], prof: list[str]) -> int:
        return len(_assemble_system(
            context=context,
            profile=profile,
            profile_memories=prof,
            dynamic_memory=dyn,
            output_spec=output_spec,
        ))

    if _size(dynamic_memory, profile_memories) <= budget:
        return dynamic_memory, profile_memories

    dyn = list(dynamic_memory)
    while dyn and _size(dyn, profile_memories) > budget:
        dyn.pop()

    if _size(dyn, profile_memories) <= budget:
        return dyn, profile_memories

    prof = list(profile_memories)
    while prof and _size(dyn, prof) > budget:
        prof.pop()

    if _size(dyn, prof) > budget:
        logger.warning(
            "system prompt still exceeds max_system_chars=%d after trimming all memory "
            "(current size=%d). Non-memory content (context/rules/identity/custom) is "
            "not trimmed; caller should shorten it.",
            budget, _size(dyn, prof),
        )
    return dyn, prof


def _assemble_system(
    *,
    context: list[str],
    profile: Profile,
    profile_memories: list[str],
    dynamic_memory: list[str],
    output_spec: OutputSpec,
) -> str:
    """Join all system prompt parts into the final string."""
    parts: list[str] = []

    for ctx in context:
        if ctx:
            parts.append(ctx)

    if profile.rules:
        parts.append(profile.rules)

    if profile.identity:
        parts.append(profile.identity)

    if profile.custom:
        parts.append(profile.custom)

    if profile_memories:
        parts.append("## Loaded Memories\n\n" + "\n".join(profile_memories))

    if dynamic_memory:
        parts.append("## Memory\n\n" + "\n".join(dynamic_memory))

    if output_spec.prompt_format:
        instruction = _FORMAT_INSTRUCTIONS.get(output_spec.prompt_format)
        if instruction:
            parts.append(instruction)

    return "\n\n".join(parts)


def _image_to_block(image: ImageInput) -> dict:
    """Convert an ImageInput to an OpenAI-format image_url content block."""
    if image.url:
        return {"type": "image_url", "image_url": {"url": image.url}}

    if image.path:
        try:
            raw = Path(image.path).read_bytes()
        except OSError as exc:
            raise ImageLoadError(image.path, str(exc)) from exc
        b64 = base64.b64encode(raw).decode()
        return {"type": "image_url", "image_url": {"url": f"data:{image.media_type};base64,{b64}"}}

    # image.data is guaranteed non-None here by ImageInput validator
    return {"type": "image_url", "image_url": {"url": f"data:{image.media_type};base64,{image.data}"}}
