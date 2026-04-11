# Profile Loading

This document defines the profile filesystem layout and loading algorithm.

Reference implementation: `priest/profile/loader.py`, `priest/profile/default_profile.py`

---

## Filesystem layout

```
{profiles_root}/
  {profile_name}/
    PROFILE.md       — required: identity and behavior text
    RULES.md         — optional: strict constraints
    CUSTOM.md        — optional: user customization layer
    profile.toml     — optional: machine-readable metadata (reserved, not consumed by engine)
    memories/        — optional directory
      *.md           — memory files (loaded, alphabetical order)
      *.txt          — memory files (loaded, alphabetical order)
```

Only `.md` and `.txt` files in the `memories/` directory are loaded. Other file types are ignored. Files are loaded in ascending lexicographic order by filename (e.g. `01-facts.md` before `02-context.md`).

---

## Loading algorithm

```
function load(name):

    if profiles_root is set:
        profile_dir = profiles_root / name
        if (profile_dir / "PROFILE.md") exists:
            return load_from_dir(name, profile_dir)

    if name == "default":
        return built_in_default_profile()

    raise PROFILE_NOT_FOUND(name)
```

### `load_from_dir(name, dir)`

```
identity = read(dir / "PROFILE.md")
rules    = read(dir / "RULES.md")    if exists else ""
custom   = read(dir / "CUSTOM.md")   if exists else ""

memories = []
if (dir / "memories") is a directory:
    files = [f for f in listdir(dir / "memories") if f ends with .md or .txt]
    sort files by filename ascending (lexicographic)
    for each file:
        memories.append(read(dir / "memories" / file))

meta = parse_toml(dir / "profile.toml") if exists else {}

return Profile(name, identity, rules, custom, memories, meta)
```

---

## Built-in default profile

When `name == "default"` and no filesystem match is found, the following hardcoded profile is returned. The content **MUST** match exactly — it is a spec-level constant.

```
identity = "You are a helpful, thoughtful assistant.\n"
rules    = "Be honest. Do not make things up.\nBe concise unless the user asks for depth.\n"
custom   = ""
memories = []
meta     = {}
```

### Override behavior

A host application can override the built-in default by providing a `default/` directory in `profiles_root`. If `profiles_root/default/PROFILE.md` exists, it takes precedence over the built-in default.

---

## Error conditions

| Condition | Error |
|-----------|-------|
| Profile name not `"default"` and not found in `profiles_root` | `PROFILE_NOT_FOUND` |
| `name == "default"`, no filesystem match, no built-in (should not occur in conforming implementations) | `PROFILE_NOT_FOUND` |

`PROFILE_INVALID` may be raised if the profile directory is structurally malformed (e.g. `PROFILE.md` exists but cannot be read). Implementations may choose to propagate the underlying I/O error instead.

---

## Notes

- Profile loading is **synchronous** — filesystem reads at startup are fast and do not benefit from async.
- The engine is handed a resolved `Profile` object and does not access the filesystem again after loading.
- Profile caching (if needed) should be implemented in the host application's `ProfileLoader` wrapper, not in the engine.
