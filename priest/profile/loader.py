from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Protocol, runtime_checkable

from priest.errors import ProfileNotFoundError
from priest.profile.default_profile import get_default_profile
from priest.profile.model import Profile


@runtime_checkable
class ProfileLoader(Protocol):
    def load(self, name: str) -> Profile: ...


# Cache key: (max mtime of any tracked file, count of tracked files).
# If either changes (edit, add, remove), the cached Profile is evicted.
_CacheKey = tuple[float, int]


class FilesystemProfileLoader:
    """Loads profiles from a directory on disk.

    profiles_root is optional. When provided, named profiles are loaded from
    that directory first. If a profile is not found there (or no root is given),
    the built-in default profile is returned for name='default'. Any other
    missing profile raises ProfileNotFoundError.

    When include_memories is false, files under memories/ are ignored. This lets
    host applications own memory assembly while still using filesystem profiles.

    Results are cached per loader instance keyed on (max mtime, file count) of
    tracked files. Any edit, add, or remove invalidates the cache on the next load.
    """

    def __init__(self, profiles_root: Path | None = None, *, include_memories: bool = True) -> None:
        self._root = profiles_root
        self._include_memories = include_memories
        self._cache: dict[str, tuple[_CacheKey, Profile]] = {}

    def load(self, name: str) -> Profile:
        if self._root is not None:
            profile_dir = self._root / name
            profile_md = profile_dir / "PROFILE.md"
            if profile_md.exists():
                return self._load_from_dir_cached(name, profile_dir)

        if name == "default":
            return get_default_profile()

        raise ProfileNotFoundError(name)

    def _load_from_dir_cached(self, name: str, profile_dir: Path) -> Profile:
        tracked = self._tracked_files(profile_dir, include_memories=self._include_memories)
        cache_key = self._cache_key(tracked)

        cached = self._cache.get(name)
        if cached is not None and cached[0] == cache_key:
            return cached[1]

        profile = self._load_from_dir(name, profile_dir)
        self._cache[name] = (cache_key, profile)
        return profile

    @staticmethod
    def _tracked_files(profile_dir: Path, *, include_memories: bool = True) -> list[Path]:
        files: list[Path] = []
        for fname in ("PROFILE.md", "RULES.md", "CUSTOM.md", "profile.toml"):
            p = profile_dir / fname
            if p.exists():
                files.append(p)
        if not include_memories:
            return files
        memories_dir = profile_dir / "memories"
        if memories_dir.is_dir():
            files.extend(
                f for f in memories_dir.iterdir()
                if f.suffix in {".md", ".txt"} and f.is_file()
            )
        return files

    @staticmethod
    def _cache_key(files: list[Path]) -> _CacheKey:
        if not files:
            return (0.0, 0)
        max_mtime = max(f.stat().st_mtime for f in files)
        return (max_mtime, len(files))

    def _load_from_dir(self, name: str, profile_dir: Path) -> Profile:
        profile_md = profile_dir / "PROFILE.md"

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
        if self._include_memories and memories_dir.is_dir():
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
