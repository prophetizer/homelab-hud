# SPDX-License-Identifier: Apache-2.0
"""Writing a credential into ``/config/secrets.yaml`` — the one place HUD ever writes one.

The wizard (PLAN §8.5) never puts a credential in a provider file: the value comes here and
the file gets ``${secret:name}``. The file is owner-only (0600) on every write, keeps its
comments and other keys (round-trip), and is never written through the config manager.
Docker secrets and environment variables win over it at resolution (§7.4); the caller says
so to the user rather than writing a value that would be ignored.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from hud.config.loader import load_yaml
from hud.config.secrets import SECRETS_FILE_NAME
from hud.config.writer import atomic_write_text, dump_yaml, has_explicit_start

SECRET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
OWNER_ONLY = 0o600
HEADER = (
    "# HUD secrets — written by the Connect a service wizard. Owner-only; keep this file\n"
    "# out of git and out of bug reports. Docker secrets and HUD_SECRET_* variables win\n"
    "# over values here.\n"
)
_lock = threading.Lock()


def store_secret(config_dir: Path, name: str, value: str) -> None:
    """Set ``name`` in secrets.yaml to ``value`` (atomic, 0600)."""
    if not SECRET_NAME.fullmatch(name):
        msg = f"secret name {name!r} must be [A-Za-z0-9_.-]"
        raise ValueError(msg)
    value = value.strip()
    if not value or "\n" in value or "\r" in value:
        msg = f"secret {name!r} must be one non-empty line"
        raise ValueError(msg)
    path = config_dir / SECRETS_FILE_NAME
    with _lock:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        doc = load_yaml(path) if text.strip() else CommentedMap()
        doc[name] = value
        body = dump_yaml(doc, explicit_start=has_explicit_start(text))
        atomic_write_text(path, body if text.strip() else HEADER + body, mode=OWNER_ONLY)


__all__ = ["SECRET_NAME", "store_secret"]
