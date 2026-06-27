from __future__ import annotations

from dataclasses import dataclass

from priest.session.model import Turn

"""Conversation compaction primitives (spec 2.5.0).

Long sessions replay their full turn history on every call (see
profile/context_builder.py), so input cost grows linearly per turn and
quadratically over a session. Compaction folds the older turns into a running
summary and replays only a recent tail, bounding the replayed history. It is
non-destructive: raw turns stay in the store; only the *replayed view* shrinks.
The summary lives in session metadata (see session/model.py).

Token accounting uses the provider's reported input usage from the previous turn
(no tokenizer dependency); the crossing turn overshoots by one, then compaction
applies before the next turn.
"""

# Compact when the previous turn's input usage exceeds this fraction of the budget.
COMPACTION_TRIGGER_RATIO = 0.8
# Most-recent turns kept verbatim; older turns fold into the summary.
DEFAULT_COMPACTION_KEEP_TURNS = 6
# Output cap for the summary-generation call (keeps the summary itself bounded).
SUMMARY_MAX_OUTPUT_TOKENS = 1024


@dataclass
class CompactionPlan:
    # Turns to fold into the summary this round (after what's already summarized,
    # before the kept tail).
    to_summarize: list[Turn]
    # Index into session.turns the new summary will cover up to.
    summarized_through: int


def should_compact(last_input_tokens: int | None, max_context_tokens: int | None) -> bool:
    """Whether the previous turn's input size warrants compaction. Off when no budget is set."""
    if not max_context_tokens or max_context_tokens <= 0:
        return False
    if last_input_tokens is None:
        return False
    return last_input_tokens > max_context_tokens * COMPACTION_TRIGGER_RATIO


def plan_compaction(
    turns: list[Turn],
    already_summarized_through: int,
    keep_turns: int,
) -> CompactionPlan | None:
    """Plan a compaction round: fold every turn after what's already summarized and
    before the kept tail. Returns None when there is nothing new to fold (history
    still fits within the kept tail), which makes repeated calls safe.
    """
    tail_start = max(0, len(turns) - max(0, keep_turns))
    if tail_start <= already_summarized_through:
        return None
    return CompactionPlan(
        to_summarize=turns[already_summarized_through:tail_start],
        summarized_through=tail_start,
    )


_SUMMARY_SYSTEM = " ".join([
    "You compress prior conversation into a compact running summary so the assistant can continue without the full transcript.",
    "Preserve the user's goals and constraints, decisions made, facts established within the conversation, and open or unresolved threads.",
    "Durable user facts are stored separately as memory — do not re-list them. Capture the conversation's trajectory and the context needed to continue it.",
    "Write a tight synopsis, not a turn-by-turn log. When an earlier summary is provided, merge the new turns into it and return a single updated summary with no preamble.",
])


def build_summary_messages(existing_summary: str | None, to_summarize: list[Turn]) -> list[dict]:
    """Build the messages for the summary-generation call (existing summary + new turns → one updated summary)."""
    transcript = "\n\n".join(f"{t.role.upper()}: {t.content}" for t in to_summarize)
    if existing_summary and existing_summary.strip():
        user = (
            f"Existing summary so far:\n\n{existing_summary.strip()}\n\n---\n\n"
            f"New conversation turns to fold in:\n\n{transcript}\n\n---\n\nReturn one updated summary."
        )
    else:
        user = f"Conversation turns to summarize:\n\n{transcript}\n\n---\n\nReturn the summary."
    return [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": user},
    ]
