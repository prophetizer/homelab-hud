# SPDX-License-Identifier: Apache-2.0
"""The ``Action`` canonical type (PLAN.md §5).

Modelled from Phase 0 so the shape is fixed; nothing dispatches an Action until Phase 3
(invariant 4: read-only posture).
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Action(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verb: str  # "restart", "pause", "search", "wake"
    resource_uid: str
    params_schema: dict[str, Any] = Field(default_factory=dict)  # JSON Schema for the UI
    confirm: bool = True
    required_permission: str
