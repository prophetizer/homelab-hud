# SPDX-License-Identifier: Apache-2.0
"""Authentication and authorization (PLAN.md §10)."""

from hud.auth.errors import AuthError
from hud.auth.principal import Principal, permission_matches
from hud.auth.rbac import Authorizer
from hud.auth.store import AuthStore, User

__all__ = ["AuthError", "AuthStore", "Authorizer", "Principal", "User", "permission_matches"]
