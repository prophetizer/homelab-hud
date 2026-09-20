# SPDX-License-Identifier: Apache-2.0
"""The one identity shape every backend produces (PLAN.md §10.1).

Downstream code never asks *how* someone authenticated; it asks the principal whether it
holds a permission. Permissions are resolved from group membership once, at resolution time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Source = Literal["local", "forward", "oidc"]


def permission_matches(granted: str, wanted: str) -> bool:
    """``*`` grants everything; a trailing ``:*`` grants every suffix; otherwise exact."""
    if granted == "*":
        return True
    if granted.endswith(":*"):
        return wanted == granted[:-2] or wanted.startswith(granted[:-1])
    return granted == wanted


@dataclass(frozen=True)
class Principal:
    subject: str
    display_name: str
    groups: frozenset[str]
    source: Source
    permissions: frozenset[str]
    csrf_token: str
    user_id: int | None = None  # None for a forward-auth identity not yet seen by the user store

    def has(self, permission: str) -> bool:
        return any(permission_matches(g, permission) for g in self.permissions)

    def has_any(self, *permissions: str) -> bool:
        return any(self.has(p) for p in permissions)

    @property
    def is_admin(self) -> bool:
        return "*" in self.permissions


__all__ = ["Principal", "Source", "permission_matches"]
