# SPDX-License-Identifier: Apache-2.0
"""Widget Engine: board bindings → renderable payloads (PLAN.md §8)."""

from hud.widgets.engine import (
    BoardSummary,
    FieldValue,
    ResolvedBoard,
    ResolvedWidget,
    WidgetEngine,
)
from hud.widgets.probe import Framing, FramingProber

__all__ = [
    "BoardSummary",
    "FieldValue",
    "Framing",
    "FramingProber",
    "ResolvedBoard",
    "ResolvedWidget",
    "WidgetEngine",
]
