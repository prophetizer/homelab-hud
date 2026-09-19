# SPDX-License-Identifier: Apache-2.0
"""The ``Resource`` canonical type (PLAN.md §5)."""

import re
from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hud.models.enums import State

# Docker short/long ids, image digests and similar content hashes. A UID built from one
# of these forks the resource's history on every recreate (§5, "UID stability").
_VOLATILE_ID = re.compile(r"^(sha256:)?[0-9a-f]{12}$|^(sha256:)?[0-9a-f]{64}$")

# Component pattern: no whitespace, no colon (the UID separator).
_COMPONENT = re.compile(r"^[^\s:]+$")


def make_uid(provider: str, kind: str, native_id: str) -> str:
    """Build a canonical UID. Raises ``ValueError`` on a volatile or malformed ``native_id``."""
    for label, part in (("provider", provider), ("kind", kind)):
        if not _COMPONENT.fullmatch(part):
            msg = f"{label} {part!r} must be non-empty with no whitespace or ':'"
            raise ValueError(msg)
    if not native_id or native_id != native_id.strip():
        msg = "native_id must be non-empty with no surrounding whitespace"
        raise ValueError(msg)
    if _VOLATILE_ID.fullmatch(native_id):
        msg = (
            f"native_id {native_id!r} looks like a content hash / container id; "
            "use a stable identifier such as the name"
        )
        raise ValueError(msg)
    return f"{provider}:{kind}:{native_id}"


class Resource(BaseModel):
    """Anything a provider can describe: a container, a host, a sensor, a download."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    uid: str = Field(description="stable: '{provider}:{kind}:{native_id}'")
    provider: str
    kind: str
    name: str
    state: State
    attrs: dict[str, Any] = Field(default_factory=dict)
    links: dict[str, str] = Field(default_factory=dict)
    parent_uid: str | None = None
    fetched_at: datetime
    stale: bool = False

    @model_validator(mode="after")
    def _uid_matches_components(self) -> Self:
        prefix = f"{self.provider}:{self.kind}:"
        if not self.uid.startswith(prefix):
            msg = f"uid {self.uid!r} must start with {prefix!r}"
            raise ValueError(msg)
        native_id = self.uid.removeprefix(prefix)
        # Re-derive through make_uid so the volatility rule is enforced on the way in.
        expected = make_uid(self.provider, self.kind, native_id)
        if expected != self.uid:
            msg = f"uid {self.uid!r} is not canonical (expected {expected!r})"
            raise ValueError(msg)
        return self

    @property
    def native_id(self) -> str:
        return self.uid.removeprefix(f"{self.provider}:{self.kind}:")
