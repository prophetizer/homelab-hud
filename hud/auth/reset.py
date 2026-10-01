# SPDX-License-Identifier: Apache-2.0
"""Reset a local account's password from the server: the way back in when it is forgotten.

    docker exec -it hud python -m hud.auth.reset <username>

Whoever can run a command inside the container already holds the data, so this asks for
nothing else. The new password is typed twice at the terminal, never echoed, logged or
taken as an argument (an argument would land in the shell history and the process list).
Every session of that account ends, and the audit log records the reset.

Exit codes: 0 reset, 1 refused (no such account, the passwords differ, too short), 2 usage.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable

from hud.auth.passwords import hash_password
from hud.auth.store import AuthStore
from hud.config import ConfigManager
from hud.settings import HudEnv
from hud.store.engine import StorePaths, create_store_engine

DEFAULT_MIN_LENGTH = 12


def min_length(env: HudEnv) -> int:
    """settings.yaml's auth.local.min_password_length; the default if it cannot be read."""
    try:
        snapshot = ConfigManager(env.config_dir).load()
    except Exception:  # a broken config must not lock the owner out
        return DEFAULT_MIN_LENGTH
    return snapshot.settings.spec.auth.local.min_password_length


def reset(
    store: AuthStore,
    username: str,
    minimum: int,
    ask: Callable[[str], str] = getpass.getpass,
) -> tuple[bool, str]:
    """Set a new password for ``username``; returns (done, what to tell the operator)."""
    subject = username.strip().lower()
    user = store.get_user_by_subject(subject, "local")
    if user is None:
        names = sorted(u.subject for u in store.list_users() if u.source == "local")
        listed = ", ".join(names) if names else "none"
        return False, f"no local account {subject!r}; local accounts: {listed}"
    first = ask(f"New password for {subject}: ")
    if len(first) < minimum:
        return False, f"too short: at least {minimum} characters"
    if ask("Again: ") != first:
        return False, "the two passwords differ; nothing changed"
    store.set_password(user.id, hash_password(first))  # also ends every session
    store.audit("cli", "auth.password_reset", subject, "ok")
    return True, f"password for {subject} reset; every session of theirs has been signed out"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hud.auth.reset",
        description="Reset a local HUD account's password (asks for it at the terminal).",
    )
    parser.add_argument("username")
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        print("run it with a terminal: docker exec -it <container> ...", file=sys.stderr)
        return 2
    env = HudEnv()
    engine = create_store_engine(StorePaths(env.data_dir))
    try:
        done, message = reset(AuthStore(engine), args.username, min_length(env))
    finally:
        engine.dispose()
    print(message, file=sys.stdout if done else sys.stderr)
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
