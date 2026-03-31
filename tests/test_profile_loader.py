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
