# SPDX-License-Identifier: Apache-2.0
"""Mask every secret HUD has resolved, wherever text leaves the process (invariant 3).

Secrets reach outbound requests as headers, basic-auth passwords and — for services that
accept nothing else (SABnzbd, Jellyfin's ``ApiKey``) — query parameters. A URL is the one
place a secret routinely escapes: HTTP libraries log it and quote it in exceptions, and a
provider's error is shown in the UI. Rather than trust every code path to leave the value
out, the resolver registers each secret it returns and two choke points mask them:
provider error messages (health, tiles, events) and the root log formatter (tracebacks
included). Redaction is by value, so it also catches paths nobody anticipated.
"""

from __future__ import annotations

import logging
import threading
from urllib.parse import quote

MASK = "[redacted]"
_MIN_LENGTH = 6  # shorter values would mangle ordinary text and are not real credentials

_lock = threading.Lock()
_needles: tuple[str, ...] = ()


def register(value: str) -> None:
    """Remember ``value`` (and its URL-encoded form) as something never to print."""
    global _needles  # noqa: PLW0603 — one process-wide set, swapped atomically
    if len(value) < _MIN_LENGTH:
        return
    forms = {value, quote(value, safe=""), quote(value)}
    with _lock:
        merged = set(_needles) | forms
        # Longest first, so a secret that contains another is masked whole.
        _needles = tuple(sorted(merged, key=len, reverse=True))


def redact(text: str) -> str:
    needles = _needles
    if not needles or not text:
        return text
    for needle in needles:
        if needle in text:
            text = text.replace(needle, MASK)
    return text


class RedactingFormatter(logging.Formatter):
    """Formats as usual, then masks — so exception tracebacks are covered too."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def install(fmt: str) -> None:
    """Put the redacting formatter on every root handler, and silence the per-request
    INFO lines httpx writes with the full URL (1,098 of them in five minutes on the live
    stack: noise today, and a key in every line once a secret rides in a query)."""
    for handler in logging.getLogger().handlers:
        handler.setFormatter(RedactingFormatter(fmt))
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


__all__ = ["MASK", "RedactingFormatter", "install", "redact", "register"]
