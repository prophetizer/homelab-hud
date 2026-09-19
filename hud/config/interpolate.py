# SPDX-License-Identifier: Apache-2.0
"""``${VAR}`` environment substitution for provider transport values (PLAN.md §7.1).

Deliberately separate from ``${secret:name}``: the secret pattern contains a colon and never
matches here, so a secret can never be resolved by this path or leak through it. Values are
resolved when a provider is *instantiated*, never at config load — the YAML on disk keeps
the reference, and the writer never sees the substituted string.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class MissingEnvVarError(LookupError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"environment variable {name!r} is not set")


def interpolate_env(text: str, env: Mapping[str, str]) -> str:
    """Replace every ``${NAME}`` in ``text``. Raises :class:`MissingEnvVarError` on the first
    unset name rather than substituting an empty string, which would silently build a wrong
    URL."""

    def _sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in env:
            raise MissingEnvVarError(name)
        return env[name]

    return ENV_REF.sub(_sub, text)


def env_refs(text: str) -> list[str]:
    """Names referenced by ``text``, in order of first appearance."""
    seen: list[str] = []
    for m in ENV_REF.finditer(text):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen
