# SPDX-License-Identifier: Apache-2.0
"""``settings.yaml`` — global options (PLAN.md §11.2). Phase 0 carries only what exists."""

import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator

from hud.config.schemas.base import Document

_DURATION = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> int:
    """``'48h'`` → seconds. Also accepts ``'forever'`` → 0 (meaning no expiry)."""
    if text == "forever":
        return 0
    m = _DURATION.fullmatch(text.strip())
    if m is None:
        msg = f"invalid duration {text!r}; use <n>s|m|h|d or 'forever'"
        raise ValueError(msg)
    return int(m.group(1)) * _UNIT_SECONDS[m.group(2)]


class Retention(BaseModel):
    """Retention per tier (PLAN.md §6.2). Stored as the user wrote them; parsed on demand."""

    model_config = ConfigDict(extra="allow")

    samples: str = "48h"
    rollup_5m: str = "14d"
    rollup_1h: str = "180d"
    rollup_1d: str = "forever"

    @field_validator("samples", "rollup_5m", "rollup_1h", "rollup_1d")
    @classmethod
    def _valid_duration(cls, v: str) -> str:
        parse_duration(v)
        return v


class SettingsSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str = "HUD"
    timezone: str = "UTC"
    theme: Literal["dark", "light", "auto"] = "dark"
    retention: Retention = Retention()

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"unknown IANA timezone {v!r}"
            raise ValueError(msg) from exc
        return v


class SettingsDocument(Document):
    kind: Literal["Settings"]
    spec: SettingsSpec = SettingsSpec()


DEFAULT_SETTINGS_YAML = """\
# HUD settings — written on first start because /config was empty.
# Everything here is optional; delete a key to fall back to its default.
apiVersion: hud/v1
kind: Settings
spec:
  title: HUD
  timezone: UTC          # IANA name, e.g. America/Chicago
  theme: dark            # dark | light | auto
  retention:             # how long each tier of history is kept (PLAN §6.2)
    samples: 48h
    rollup_5m: 14d
    rollup_1h: 180d
    rollup_1d: forever
"""
