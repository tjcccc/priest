#!/usr/bin/env python3
"""Send a prompt to a local Ollama model via priest, or run the built-in smoke test.

Usage:
    # Single prompt
    python scripts/try_run.py --model qwen3.5:9b --prompt "hello, what's your model"

    # Multi-turn chat session
    python scripts/try_run.py --model qwen3.5:9b --chat

    # Built-in smoke test (no --prompt or --chat needed)
    python scripts/try_run.py --model qwen3.5:9b

Options:
    --model MODEL       Ollama model name (default: qwen3.5:9b)
    --prompt PROMPT     Send a single prompt and print the response
    --chat              Interactive multi-turn chat session
    --profile PROFILE   Profile name to use (default: default)
    --think             Enable model thinking mode (disabled by default)
    --url URL           Ollama base URL (default: http://localhost:11434)
    --profiles-dir DIR  Profiles directory (default: profiles/)
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from priest import PriestConfig, PriestEngine, PriestRequest, SessionRef
from priest.profile.loader import FilesystemProfileLoader
from priest.providers.ollama_provider import OllamaProvider
from priest.session.memory_store import InMemorySessionStore
from priest.session.sqlite_store import SqliteSessionStore

SESSION_DB = Path("/tmp/priest_try_run.db")


def _resolve_profiles(profiles_dir: Path) -> Path:
    if profiles_dir.exists():
        return profiles_dir
    fallback = Path(__file__).parent.parent / "tests" / "fixtures" / "profiles"
    print(f"[warn] Profiles directory not found: {profiles_dir}")
    print(f"       Falling back to: {fallback}")
    return fallback


def _make_config(args: argparse.Namespace) -> PriestConfig:
    options = {} if args.think else {"think": False}
    return PriestConfig(
        provider="ollama",
        model=args.model,
        timeout_seconds=120.0,
        provider_options=options,
    )


async def single_prompt(args: argparse.Namespace) -> None:
    cfg = _make_config(args)
    profiles_dir = _resolve_profiles(Path(args.profiles_dir))

    engine = PriestEngine(
        profile_loader=FilesystemProfileLoader(profiles_dir),
        adapters={"ollama": OllamaProvider(base_url=args.url)},
    )

    response = await engine.run(PriestRequest(
        config=cfg,
        profile=args.profile,
        prompt=args.prompt,
    ))

    if not response.ok:
        print(f"[error] {response.error}")
        sys.exit(1)

    print(response.text)
    if response.usage:
        print(f"\n[{response.execution.latency_ms}ms | in={response.usage.input_tokens} out={response.usage.output_tokens}]")


async def chat_session(args: argparse.Namespace) -> None:
    cfg = _make_config(args)
    profiles_dir = _resolve_profiles(Path(args.profiles_dir))

    print(f"Model  : {args.model}")
    print(f"Profile: {args.profile}")
    print("Type 'exit' or Ctrl-C to quit.\n")

    store = InMemorySessionStore()
    session = await store.create(profile_name=args.profile)

    engine = PriestEngine(
        profile_loader=FilesystemProfileLoader(profiles_dir),
        session_store=store,
        adapters={"ollama": OllamaProvider(base_url=args.url)},
    )

    while True:
        try:
            prompt = input("you: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if prompt.lower() in {"exit", "quit"}:
            break
        if not prompt:
            continue

        response = await engine.run(PriestRequest(
            config=cfg,
            profile=args.profile,
            prompt=prompt,
            session=SessionRef(id=session.id),
        ))

        if not response.ok:
            print(f"[error] {response.error}")
            continue

        print(f"model: {response.text}")
        print()


async def smoke_test(args: argparse.Namespace) -> None:
    cfg = _make_config(args)
    profiles_dir = _resolve_profiles(Path(args.profiles_dir))

    print(f"Model   : {args.model}")
    print(f"Ollama  : {args.url}")
    print(f"Profiles: {args.profiles_dir}")
    print()

    async with SqliteSessionStore(db_path=SESSION_DB) as store:
        engine = PriestEngine(
            profile_loader=FilesystemProfileLoader(profiles_dir),
            session_store=store,
            adapters={"ollama": OllamaProvider(base_url=args.url)},
        )

        print("--- Run 1: basic prompt ---")
        r = await engine.run(PriestRequest(
            config=cfg,
            profile=args.profile,
            prompt="Say exactly: 'Smoke test passed.'",
        ))
        if not r.ok:
            print(f"[FAIL] {r.error}")
            sys.exit(1)
        print(f"Response : {r.text!r}")
        print(f"Latency  : {r.execution.latency_ms}ms")
        if r.usage:
            print(f"Tokens   : in={r.usage.input_tokens} out={r.usage.output_tokens}")
        print()

        print("--- Run 2: session continuation ---")
        session = await store.create(profile_name=args.profile)
        print(f"Session  : {session.id}")

        r1 = await engine.run(PriestRequest(
            config=cfg, profile=args.profile,
            prompt="Remember this number: 7.",
            session=SessionRef(id=session.id),
        ))
        if not r1.ok:
            print(f"[FAIL] {r1.error}")
            sys.exit(1)
        print(f"Turn 1   : {r1.text!r}")

        r2 = await engine.run(PriestRequest(
            config=cfg, profile=args.profile,
            prompt="What is the number I asked you to remember?",
            session=SessionRef(id=session.id),
        ))
        if not r2.ok:
            print(f"[FAIL] {r2.error}")
            sys.exit(1)
        print(f"Turn 2   : {r2.text!r}")
        print()

    print("[PASS] Smoke test complete.")
    SESSION_DB.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test priest against a local Ollama model.")
    parser.add_argument("--model", default="qwen3.5:9b", help="Ollama model name")
    parser.add_argument("--prompt", default=None, help="Single prompt to send")
    parser.add_argument("--chat", action="store_true", help="Interactive chat session")
    parser.add_argument("--profile", default="default", help="Profile name")
    parser.add_argument("--think", action="store_true", help="Enable thinking mode")
    parser.add_argument("--url", default="http://localhost:11434", help="Ollama base URL")
    parser.add_argument("--profiles-dir", default="profiles", help="Profiles directory")
    args = parser.parse_args()

    if args.prompt:
        asyncio.run(single_prompt(args))
    elif args.chat:
        asyncio.run(chat_session(args))
    else:
        asyncio.run(smoke_test(args))


if __name__ == "__main__":
    main()
