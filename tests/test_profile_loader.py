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
