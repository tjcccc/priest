from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Profile:
    name: str
    identity: str          # content of PROFILE.md
    rules: str             # content of RULES.md (empty string if absent)
    custom: str            # content of CUSTOM.md (empty string if absent)
    memories: list[str]    # contents of files in memories/, sorted by filename
    meta: dict             # parsed profile.toml (empty dict if absent)

    def __post_init__(self) -> None:
        # Ensure memories is always a list, never None
        if self.memories is None:
            self.memories = []
