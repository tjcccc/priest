"""Built-in fallback default profile.

Used when the host application does not provide a 'default' profile.
Host apps can override this by placing a 'default' folder in their
profiles_root directory.
"""

from priest.profile.model import Profile

IDENTITY = """\
You are a helpful, thoughtful assistant.
"""

RULES = """\
Be honest. Do not make things up.
Be concise unless the user asks for depth.
"""

CUSTOM = ""

MEMORIES: list[str] = []

META: dict = {}


def get_default_profile() -> Profile:
    return Profile(
        name="default",
        identity=IDENTITY,
        rules=RULES,
        custom=CUSTOM,
        memories=MEMORIES,
        meta=META,
    )
