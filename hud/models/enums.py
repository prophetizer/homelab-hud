# SPDX-License-Identifier: Apache-2.0
"""Closed vocabularies shared by every canonical type (PLAN.md §5)."""

from enum import StrEnum


class State(StrEnum):
    UP = "up"
    DOWN = "down"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"
    PAUSED = "paused"


class Unit(StrEnum):
    """Canonical storage units. Anything a provider reports is converted to one of these."""

    PCT = "pct"  # 0-100
    BYTES = "bytes"
    BPS = "bps"  # bits per second
    COUNT = "count"
    SECONDS = "seconds"
    CELSIUS = "celsius"
    WATTS = "watts"
    NONE = "none"


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
