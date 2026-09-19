# SPDX-License-Identifier: Apache-2.0
"""The ``Metric`` canonical type (PLAN.md §5). Values are already normalized."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from hud.models.enums import Unit
from hud.models.units import SourceUnit, normalize


class Metric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    resource_uid: str
    name: str  # "cpu_pct", "mem_bytes", "queue_size", "rx_bps"
    value: float
    unit: Unit
    ts: datetime

    @classmethod
    def from_source(
        cls, *, resource_uid: str, name: str, value: float, source_unit: SourceUnit, ts: datetime
    ) -> "Metric":
        """Boundary constructor: normalize ``value`` from ``source_unit`` on the way in."""
        norm_value, unit = normalize(value, source_unit)
        return cls(resource_uid=resource_uid, name=name, value=norm_value, unit=unit, ts=ts)
