from pathlib import Path

import pytest

from priest.errors import ProfileNotFoundError
from priest.profile.loader import FilesystemProfileLoader

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"


def test_load_default_profile():
    loader = FilesystemProfileLoader(FIXTURES)
    profile = loader.load("default")

    assert profile.name == "default"
    assert "helpful" in profile.identity
    assert "concise" in profile.rules.lower()
    assert "short answers" in profile.custom.lower()
    assert len(profile.memories) == 1
    assert "test memory" in profile.memories[0]


def test_load_profile_can_skip_memories():
    loader = FilesystemProfileLoader(FIXTURES, include_memories=False)
    profile = loader.load("default")

    assert profile.name == "default"
    assert profile.memories == []


def test_load_minimal_profile():
    loader = FilesystemProfileLoader(FIXTURES)
    profile = loader.load("minimal")

    assert profile.name == "minimal"
    assert profile.identity.strip() != ""
    assert profile.rules == ""
    assert profile.custom == ""
    assert profile.memories == []
    assert profile.meta == {}


def test_load_missing_profile_raises():
    loader = FilesystemProfileLoader(FIXTURES)
    with pytest.raises(ProfileNotFoundError) as exc_info:
        loader.load("nonexistent")
    assert "nonexistent" in exc_info.value.message


def test_builtin_default_used_when_no_profiles_root():
    loader = FilesystemProfileLoader()
    profile = loader.load("default")

    assert profile.name == "default"
    assert profile.identity.strip() != ""
    assert profile.rules.strip() != ""


def test_builtin_default_used_when_not_overridden_in_profiles_root(tmp_path):
    # profiles_root exists but has no 'default' folder
    loader = FilesystemProfileLoader(tmp_path)
    profile = loader.load("default")

    assert profile.name == "default"


def test_app_default_overrides_builtin(tmp_path):
    # Host app provides its own 'default' profile — it should take precedence
    default_dir = tmp_path / "default"
    default_dir.mkdir()
    (default_dir / "PROFILE.md").write_text("Custom app identity.")

    loader = FilesystemProfileLoader(tmp_path)
    profile = loader.load("default")

    assert profile.identity == "Custom app identity."


def test_missing_non_default_profile_raises_without_root():
    loader = FilesystemProfileLoader()
    with pytest.raises(ProfileNotFoundError):
        loader.load("nonexistent")


def test_loader_caches_profile(tmp_path):
    """Second load with unchanged files returns the cached Profile instance."""
    profile_dir = tmp_path / "cached"
    profile_dir.mkdir()
    (profile_dir / "PROFILE.md").write_text("Identity.")

    loader = FilesystemProfileLoader(tmp_path)
    p1 = loader.load("cached")
    p2 = loader.load("cached")

    assert p1 is p2  # same object — cache hit


def test_loader_invalidates_on_mtime_change(tmp_path):
    """Editing any tracked file invalidates the cache."""
    import time

    profile_dir = tmp_path / "edit"
    profile_dir.mkdir()
    profile_md = profile_dir / "PROFILE.md"
    profile_md.write_text("First.")

    loader = FilesystemProfileLoader(tmp_path)
    p1 = loader.load("edit")

    # Ensure a visible mtime bump across platforms with coarse FS timestamps.
    time.sleep(0.01)
    profile_md.write_text("Second.")
    import os as _os
    now = time.time()
    _os.utime(profile_md, (now, now + 1))

    p2 = loader.load("edit")
    assert p2 is not p1
    assert "Second." in p2.identity


def test_loader_invalidates_on_memory_file_added(tmp_path):
    """Adding a new memory file invalidates the cache."""
    profile_dir = tmp_path / "addmem"
    (profile_dir / "memories").mkdir(parents=True)
    (profile_dir / "PROFILE.md").write_text("Ident.")
    (profile_dir / "memories" / "01.md").write_text("One.")

    loader = FilesystemProfileLoader(tmp_path)
    p1 = loader.load("addmem")
    assert len(p1.memories) == 1

    (profile_dir / "memories" / "02.md").write_text("Two.")
    p2 = loader.load("addmem")
    assert p2 is not p1
    assert len(p2.memories) == 2


def test_loader_does_not_track_memory_files_when_skipped(tmp_path):
    """Adding a memory file does not invalidate cache when include_memories=False."""
    profile_dir = tmp_path / "skipmem"
    (profile_dir / "memories").mkdir(parents=True)
    (profile_dir / "PROFILE.md").write_text("Ident.")
    (profile_dir / "memories" / "01.md").write_text("One.")

    loader = FilesystemProfileLoader(tmp_path, include_memories=False)
    p1 = loader.load("skipmem")
    assert p1.memories == []

    (profile_dir / "memories" / "02.md").write_text("Two.")
    p2 = loader.load("skipmem")
    assert p2 is p1
    assert p2.memories == []
