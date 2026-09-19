# SPDX-License-Identifier: Apache-2.0
"""The ``Event`` canonical type (PLAN.md §5)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from hud.models.enums import Severity


class Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    resource_uid: str
    type: str  # "state_change", "error", "import", "update_available"
    severity: Severity
    message: str
    ts: datetime
