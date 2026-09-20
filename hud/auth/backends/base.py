# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import dataclass, field

from hud.auth.principal import Source


@dataclass(frozen=True)
class ExternalIdentity:
    """What a backend knows about the caller before RBAC is applied."""

    subject: str
    source: Source
    display_name: str | None = None
    email: str | None = None
    groups: frozenset[str] = field(default_factory=frozenset)


__all__ = ["ExternalIdentity"]
