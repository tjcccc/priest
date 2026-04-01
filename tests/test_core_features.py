"""Core feature tests for the priest library.

Tests 1-3: unit tests — no external dependencies required.
Test 4:    integration test — requires a running Ollama instance.

Run unit tests only:
    uv run pytest tests/test_core_features.py -m "not integration"

Run all including integration:
    uv run pytest tests/test_core_features.py
"""

import os
from pathlib import Path

import pytest

from priest import PriestConfig, PriestEngine, PriestRequest, SessionRef
from priest.profile.default_profile import get_default_profile
from priest.profile.loader import FilesystemProfileLoader
from priest.profile.model import Profile
from priest.providers.ollama_provider import OllamaProvider
from priest.session.memory_store import InMemorySessionStore
from priest.session.sqlite_store import SqliteSessionStore
from tests.mock_adapter import MockAdapter

OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"


# ---------------------------------------------------------------------------
# 1. Profile loading
# ---------------------------------------------------------------------------

class TestProfileLoading:
    def test_builtin_default_profile_has_content(self):
        """Built-in default profile exists and has non-empty identity and rules."""
        profile = get_default_profile()
        assert profile.name == "default"
        assert profile.identity.strip() != ""
        assert profile.rules.strip() != ""

    def test_load_profile_from_filesystem(self):
        """FilesystemProfileLoader correctly reads all profile files."""
        loader = FilesystemProfileLoader(FIXTURES)
        profile = loader.load("default")

        assert profile.name == "default"
        assert profile.identity.strip() != ""
        assert profile.rules.strip() != ""
        assert profile.custom.strip() != ""
        assert len(profile.memories) == 1

    def test_profile_constructed_from_strings(self):
        """A Profile can be built directly from strings — no filesystem needed."""
        profile = Profile(
            name="custom",
            identity="You are a pirate assistant.",
            rules="Always say 'Arrr'.",
            custom="",
            memories=["The treasure is buried on the island."],
            meta={},
        )
        assert profile.name == "custom"
        assert "pirate" in profile.identity
        assert len(profile.memories) == 1

    def test_app_profile_overrides_builtin_default(self, tmp_path):
        """App-provided default profile takes precedence over built-in."""
        default_dir = tmp_path / "default"
        default_dir.mkdir()
        (default_dir / "PROFILE.md").write_text("You are a specialized app assistant.")

        loader = FilesystemProfileLoader(tmp_path)
        profile = loader.load("default")

        assert "specialized app assistant" in profile.identity

    def test_fallback_to_builtin_when_no_app_default(self, tmp_path):
        """Falls back to built-in default when app provides no default profile."""
        loader = FilesystemProfileLoader(tmp_path)
        profile = loader.load("default")
        assert profile.name == "default"
        assert profile.identity.strip() != ""


# ---------------------------------------------------------------------------
# 2. Session save and load
# ---------------------------------------------------------------------------

class TestSession:
    @pytest.mark.asyncio
    async def test_session_created_and_persisted(self, tmp_path):
        """Session is created and turns are persisted to SQLite."""
        db = tmp_path / "sessions.db"
        async with SqliteSessionStore(db_path=db) as store:
            session = await store.create(profile_name="default")
            session.append_turn("user", "Hello.")
            session.append_turn("assistant", "Hi there.")
            await store.save(session)

            loaded = await store.get(session.id)
            assert loaded is not None
            assert len(loaded.turns) == 2
            assert loaded.turns[0].role == "user"
            assert loaded.turns[0].content == "Hello."
            assert loaded.turns[1].role == "assistant"
            assert loaded.turns[1].content == "Hi there."

    @pytest.mark.asyncio
    async def test_session_persists_across_store_instances(self, tmp_path):
        """Session written by one store instance is readable by another."""
        db = tmp_path / "sessions.db"

        async with SqliteSessionStore(db_path=db) as store:
            session = await store.create(profile_name="default")
            session_id = session.id
            session.append_turn("user", "Remember: blue.")
            await store.save(session)

        # New store instance, same file
        async with SqliteSessionStore(db_path=db) as store2:
            loaded = await store2.get(session_id)
            assert loaded is not None
            assert loaded.turns[0].content == "Remember: blue."

    @pytest.mark.asyncio
    async def test_session_not_found_returns_none(self, tmp_path):
        db = tmp_path / "sessions.db"
        async with SqliteSessionStore(db_path=db) as store:
            result = await store.get("nonexistent-id")
            assert result is None

    @pytest.mark.asyncio
    async def test_engine_continues_session_across_runs(self):
        """Engine appends turns to session across two separate run() calls."""
        store = InMemorySessionStore()
        engine = PriestEngine(
            profile_loader=FilesystemProfileLoader(),
            session_store=store,
            adapters={"mock": MockAdapter(text="Got it.")},
        )

        r1 = await engine.run(PriestRequest(
            config=PriestConfig(provider="mock", model="test"),
            prompt="First message.",
            session=SessionRef(id="s1", create_if_missing=True),
        ))
        session_id = r1.session.id

        r2 = await engine.run(PriestRequest(
            config=PriestConfig(provider="mock", model="test"),
            prompt="Second message.",
            session=SessionRef(id=session_id),
        ))

        saved = await store.get(session_id)
        assert saved is not None
        assert len(saved.turns) == 4  # user + assistant × 2
        assert r2.session.turn_count == 4


# ---------------------------------------------------------------------------
# 3. Memories in context
# ---------------------------------------------------------------------------

class TestMemories:
    @pytest.mark.asyncio
    async def test_memories_included_in_system_message(self):
        """Memory content from profile is present in the system prompt sent to provider."""
        from unittest.mock import patch
        from priest.profile.context_builder import build_messages

        profile = Profile(
            name="test",
            identity="You are an assistant.",
            rules="",
            custom="",
            memories=[
                "The user's favourite colour is indigo.",
                "The user is a software engineer.",
            ],
            meta={},
        )

        messages = build_messages(
            profile=profile,
            session=None,
            prompt="Hello.",
            system_context=[],
            extra_context=[],
            output_spec=__import__(
                "priest.schema.request", fromlist=["OutputSpec"]
            ).OutputSpec(),
        )

        system_msg = next(m for m in messages if m["role"] == "system")
        assert "indigo" in system_msg["content"]
        assert "software engineer" in system_msg["content"]

    @pytest.mark.asyncio
    async def test_memories_loaded_from_filesystem(self, tmp_path):
        """Memories directory files are loaded and included in the profile."""
        profile_dir = tmp_path / "myprofile"
        (profile_dir / "memories").mkdir(parents=True)
        (profile_dir / "PROFILE.md").write_text("You are an assistant.")
        (profile_dir / "memories" / "01_fact.md").write_text("The secret word is MANGO.")

        loader = FilesystemProfileLoader(tmp_path)
        profile = loader.load("myprofile")

        assert len(profile.memories) == 1
        assert "MANGO" in profile.memories[0]

    @pytest.mark.asyncio
    async def test_system_context_appears_before_memories(self):
        """App-layer system_context is injected above profile memories."""
        from priest.profile.context_builder import build_messages
        from priest.schema.request import OutputSpec

        profile = Profile(
            name="test",
            identity="",
            rules="",
            custom="",
            memories=["Memory: the sky is blue."],
            meta={},
        )

        messages = build_messages(
            profile=profile,
            session=None,
            prompt="Hello.",
            system_context=["App policy: be brief."],
            extra_context=[],
            output_spec=OutputSpec(),
        )

        system_msg = next(m for m in messages if m["role"] == "system")
        content = system_msg["content"]
        assert content.index("App policy") < content.index("Memory:")


# ---------------------------------------------------------------------------
# 4. Real AI conversation follows context and memories  [integration]
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestAIConversation:
    """Requires a running Ollama instance. Skip with: -m 'not integration'"""

    def _engine(self, store=None, memories=None, rules=""):
        profile = Profile(
            name="test",
            identity="You are a helpful assistant. Answer questions directly and concisely.",
            rules=rules,
            custom="",
            memories=memories or [],
            meta={},
        )

        class FixedProfileLoader:
            def load(self, name: str) -> Profile:
                return profile

        return PriestEngine(
            profile_loader=FixedProfileLoader(),
            session_store=store,
            adapters={
                "ollama": OllamaProvider(base_url=OLLAMA_URL),
            },
        )

    def _cfg(self) -> PriestConfig:
        return PriestConfig(
            provider="ollama",
            model=OLLAMA_MODEL,
            timeout_seconds=120.0,
            provider_options={"think": False},
        )

    @pytest.mark.asyncio
    async def test_ai_follows_memory_from_profile(self):
        """AI uses a fact from profile memories to answer a direct question."""
        engine = self._engine(memories=["The user's name is Atlas."])

        response = await engine.run(PriestRequest(
            config=self._cfg(),
            prompt="What is my name? Reply with only the name, nothing else.",
        ))

        assert response.ok, f"Error: {response.error}"
        assert "atlas" in response.text.lower()

    @pytest.mark.asyncio
    async def test_ai_follows_rules_from_profile(self):
        """AI respects a hard rule defined in the profile."""
        engine = self._engine(rules="You must always respond in exactly one word.")

        response = await engine.run(PriestRequest(
            config=self._cfg(),
            prompt="What is the capital of France?",
        ))

        assert response.ok, f"Error: {response.error}"
        assert len(response.text.strip().split()) == 1

    @pytest.mark.asyncio
    async def test_ai_follows_system_context(self):
        """AI uses app-injected system_context to answer a question."""
        engine = self._engine()

        response = await engine.run(PriestRequest(
            config=self._cfg(),
            prompt="What is today's date? Reply with only the date.",
            system_context=["Today's date is 2099-01-15."],
        ))

        assert response.ok, f"Error: {response.error}"
        assert "2099" in response.text

    @pytest.mark.asyncio
    async def test_ai_remembers_across_session_turns(self):
        """AI recalls a fact from an earlier turn in the same session."""
        store = InMemorySessionStore()
        engine = self._engine(store=store)

        r1 = await engine.run(PriestRequest(
            config=self._cfg(),
            prompt="Remember this code word: SUNFLOWER. Confirm you have noted it.",
            session=SessionRef(id="test-session", create_if_missing=True),
        ))
        assert r1.ok, f"Turn 1 error: {r1.error}"
        session_id = r1.session.id

        r2 = await engine.run(PriestRequest(
            config=self._cfg(),
            prompt="What was the code word I asked you to remember? Reply with only the word.",
            session=SessionRef(id=session_id),
        ))
        assert r2.ok, f"Turn 2 error: {r2.error}"
        assert "sunflower" in r2.text.lower()
