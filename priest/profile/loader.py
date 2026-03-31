from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Protocol, runtime_checkable

from priest.errors import ProfileNotFoundError
from priest.profile.model import Profile


@runtime_checkable
class ProfileLoader(Protocol):
    def load(self, name: str) -> Profile: ...


class FilesystemProfileLoader:
    """Loads profiles from a directory on disk.

    The host application provides the profiles_root path. Core never
    hardcodes a default path.
    """

    def __init__(self, profiles_root: Path) -> None:
        self._root = profiles_root

    def load(self, name: str) -> Profile:
        profile_dir = self._root / name

        profile_md = profile_dir / "PROFILE.md"
        if not profile_md.exists():
            raise ProfileNotFoundError(name)

        identity = profile_md.read_text(encoding="utf-8")

        rules_md = profile_dir / "RULES.md"
        rules = rules_md.read_text(encoding="utf-8") if rules_md.exists() else ""

        custom_md = profile_dir / "CUSTOM.md"
        custom = custom_md.read_text(encoding="utf-8") if custom_md.exists() else ""

        toml_path = profile_dir / "profile.toml"
        if toml_path.exists():
            meta: dict = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        else:
            meta = {}

        memories: list[str] = []
        memories_dir = profile_dir / "memories"
        if memories_dir.is_dir():
            memory_files = sorted(
                f for f in memories_dir.iterdir()
                if f.suffix in {".md", ".txt"} and f.is_file()
            )
            for mf in memory_files:
                memories.append(mf.read_text(encoding="utf-8"))

        return Profile(
            name=name,
            identity=identity,
            rules=rules,
            custom=custom,
            memories=memories,
            meta=meta,
        )
