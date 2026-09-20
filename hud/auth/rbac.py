# SPDX-License-Identifier: Apache-2.0
"""Group → permission resolution and the board visibility rule (PLAN.md §10.2).

A board is viewable when the caller holds ``boards:view:<name>`` (directly or through a
wildcard) **or** one of the caller's groups is listed in the board's ``visible_to``. The two
mechanisms in the plan are additive; neither restricts the other.
"""

from __future__ import annotations

from hud.auth.principal import Principal
from hud.config.schemas import BoardDocument, RbacSpec


class Authorizer:
    def __init__(self, spec: RbacSpec) -> None:
        self._spec = spec

    @property
    def spec(self) -> RbacSpec:
        return self._spec

    def permissions_for(self, groups: frozenset[str] | set[str]) -> frozenset[str]:
        """Union of grants for every listed group; ``defaults.unmatched_group`` applies only
        when *none* of the caller's groups is listed."""
        listed = [g for g in groups if g in self._spec.groups]
        if not listed:
            listed = [self._spec.defaults.unmatched_group]
        out: set[str] = set()
        for g in listed:
            grp = self._spec.groups.get(g)
            if grp is not None:
                out.update(grp.permissions)
        return frozenset(out)

    @staticmethod
    def can_view_board(principal: Principal, board: BoardDocument) -> bool:
        name = board.metadata.name
        if principal.has(f"boards:view:{name}"):
            return True
        return bool(principal.groups & set(board.metadata.visible_to))

    @staticmethod
    def can_edit_board(principal: Principal, board: BoardDocument) -> bool:
        return principal.has(f"boards:edit:{board.metadata.name}")


__all__ = ["Authorizer"]
