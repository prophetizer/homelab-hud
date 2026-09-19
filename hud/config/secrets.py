# SPDX-License-Identifier: Apache-2.0
"""``${secret:name}`` resolution and the literal-token detector (PLAN.md §7.4).

Resolution order: ``/run/secrets/<name>`` → env ``HUD_SECRET_<NAME>`` → ``secrets.yaml``.
Resolved values live only in memory; nothing here ever hands one to the YAML writer.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from hud.config.errors import ConfigIssue
from hud.config.loader import load_yaml

SECRET_REF = re.compile(r"^\$\{secret:([A-Za-z0-9][A-Za-z0-9_.-]*)\}$")
ENV_PREFIX = "HUD_SECRET_"
SECRETS_FILE_NAME = "secrets.yaml"

# Keys whose string values must be a ${secret:...} reference, never a literal.
_SENSITIVE_KEY = re.compile(
    r"(^|_|-)(token|secret|password|passwd|pass|api[_-]?key|apikey|private[_-]?key|"
    r"client[_-]?secret|auth|credentials?)$",
    re.IGNORECASE,
)

# Value shapes that are a credential regardless of the key they sit under.
_TOKEN_VALUE = re.compile(
    r"^(?:"
    r"[A-Fa-f0-9]{32,}"  # hex API keys (Sonarr/Radarr/Prowlarr/Home Assistant long-lived…)
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"  # JWT
    r"|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}"  # GitHub
    r"|glpat-[A-Za-z0-9_-]{20,}"  # GitLab
    r"|xox[abpr]-[A-Za-z0-9-]{10,}"  # Slack
    r"|sk-[A-Za-z0-9_-]{20,}"  # OpenAI-style
    r"|AKIA[0-9A-Z]{16}"  # AWS access key id
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r")"
)


def is_secret_ref(value: object) -> bool:
    return isinstance(value, str) and SECRET_REF.fullmatch(value) is not None


def secret_name(value: str) -> str:
    m = SECRET_REF.fullmatch(value)
    if m is None:
        msg = f"not a secret reference: {value!r}"
        raise ValueError(msg)
    return m.group(1)


def looks_like_literal_secret(key: str | None, value: object) -> bool:
    if not isinstance(value, str) or not value or is_secret_ref(value):
        return False
    if _TOKEN_VALUE.match(value):
        return True
    return key is not None and _SENSITIVE_KEY.search(key) is not None


def scan_literal_secrets(doc: Any, file: Path) -> list[ConfigIssue]:  # noqa: ANN401
    """Every literal-looking credential in ``doc`` with its position. Non-empty ⇒ hard fail."""
    scanner = _Scanner(file)
    scanner.walk(doc, None, ())
    return scanner.issues


class _Scanner:
    def __init__(self, file: Path) -> None:
        self.file = file
        self.issues: list[ConfigIssue] = []

    def walk(
        self,
        node: Any,  # noqa: ANN401
        key: str | None,
        path: tuple[str, ...],
        pos: tuple[int, int] | None = None,
    ) -> None:
        if isinstance(node, CommentedMap):
            for k, v in node.items():
                k_str = str(k)
                self.walk(v, k_str, (*path, k_str), _pos(node, k, "value"))
        elif isinstance(node, CommentedSeq):
            for i, v in enumerate(node):
                self.walk(v, key, (*path, str(i)), _pos(node, i, "item"))
        elif looks_like_literal_secret(key, node):
            line, col = (pos[0] + 1, pos[1] + 1) if pos else (None, None)
            dotted = ".".join(path)
            self.issues.append(
                ConfigIssue(
                    self.file,
                    f"'{dotted}' looks like a literal secret; use ${{secret:<name>}} instead "
                    f"(secrets are never stored in YAML)",
                    line,
                    col,
                )
            )


def _pos(node: CommentedMap | CommentedSeq, k: Any, kind: str) -> tuple[int, int] | None:  # noqa: ANN401
    """Position of a key/item, or None for nodes added programmatically (no source line)."""
    try:
        pos = getattr(node.lc, kind)(k)
    except (KeyError, IndexError, AttributeError, TypeError):
        return None
    return (int(pos[0]), int(pos[1])) if pos else None


class SecretNotFoundError(LookupError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"secret '{name}' not found in /run/secrets, ${ENV_PREFIX}{name.upper()} "
            f"or {SECRETS_FILE_NAME}"
        )


class SecretResolver:
    def __init__(
        self,
        config_dir: Path,
        secrets_dir: Path = Path("/run/secrets"),
        env: dict[str, str] | None = None,
    ) -> None:
        self._secrets_dir = secrets_dir
        self._file = config_dir / SECRETS_FILE_NAME
        self._env = env if env is not None else dict(os.environ)

    def resolve(self, name: str) -> str:
        docker = self._secrets_dir / name
        if docker.is_file():
            return docker.read_text(encoding="utf-8").rstrip("\r\n")
        env_key = ENV_PREFIX + re.sub(r"[^A-Za-z0-9]", "_", name).upper()
        if env_key in self._env:
            return self._env[env_key]
        if self._file.is_file():
            doc = load_yaml(self._file)
            value = doc.get(name)
            if isinstance(value, str | int | float) and not isinstance(value, bool):
                return str(value)
        raise SecretNotFoundError(name)

    def resolve_refs(self, value: Any) -> Any:  # noqa: ANN401
        """Deep-copy ``value`` with every ``${secret:name}`` replaced. Plain containers out."""
        if isinstance(value, str):
            return self.resolve(secret_name(value)) if is_secret_ref(value) else value
        if isinstance(value, dict):
            return {k: self.resolve_refs(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve_refs(v) for v in value]
        return value
