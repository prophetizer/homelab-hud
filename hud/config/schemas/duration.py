# SPDX-License-Identifier: Apache-2.0
"""Durations in config (``48h``, ``30s``, ``forever``). Its own module so every schema
can use it without importing another schema."""

import re

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
