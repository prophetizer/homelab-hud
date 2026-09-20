# SPDX-License-Identifier: Apache-2.0
"""``rbac.yaml`` — group → permission grants (PLAN.md §10.2).

Permissions are strings. A grant of ``*`` matches everything; a trailing ``*`` segment matches
any suffix (``boards:view:*``). Group names come from whichever auth backend produced the
principal, so switching IdPs means editing names here, not code.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hud.config.schemas.base import Document

_PERMISSION = re.compile(r"^(\*|[a-z0-9_-]+(:[a-z0-9_.-]+)*(:\*)?)$")


class RbacGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permissions: list[str] = Field(default_factory=list)

    @field_validator("permissions")
    @classmethod
    def _permission_shape(cls, v: list[str]) -> list[str]:
        for p in v:
            if not _PERMISSION.fullmatch(p):
                msg = f"permission {p!r} must look like scope:action[:target] or '*'"
                raise ValueError(msg)
        return v


class RbacDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unmatched_group: str = "guests"


class RbacSpec(BaseModel):
    """``defaults.unmatched_group`` need not be listed under ``groups``: an unlisted group
    grants nothing, which is the safe reading."""

    model_config = ConfigDict(extra="allow")

    groups: dict[str, RbacGroup] = Field(default_factory=dict)
    defaults: RbacDefaults = RbacDefaults()


class RbacDocument(Document):
    kind: Literal["RBAC"]
    spec: RbacSpec = RbacSpec()


DEFAULT_RBAC_YAML = """\
# HUD access control — written on first start because /config had no RBAC document.
# Group names come from your auth backend (local users, proxy headers, or an OIDC claim).
# The first local account created at setup is placed in `admins`.
apiVersion: hud/v1
kind: RBAC
spec:
  groups:
    admins:
      permissions: ["*"]
    household:
      permissions: ["boards:view:*"]    # or name boards: boards:view:media
    guests:
      permissions: []                   # a group with no grants sees nothing
  defaults:
    unmatched_group: guests             # applied when none of a user's groups is listed above
"""
