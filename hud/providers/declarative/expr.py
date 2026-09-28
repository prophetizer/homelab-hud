# SPDX-License-Identifier: Apache-2.0
"""Sandboxed expression evaluation for the declarative spec (PLAN.md §7.1, R5).

Every ``{{ … }}`` in a Provider document is compiled once into a :class:`Expr` and rendered
per item. The environment is Jinja's *immutable sandbox* — no loader, no ``include`` or
``import``, no attribute access into dunders or mutating methods, ``range`` capped — with
native-typed results so ``{{ item.state | float }}`` yields a float rather than a string.

There is no per-expression CPU interrupt: Jinja cannot be pre-empted mid-render. The
bounds are structural (sandbox limits, bounded input, no recursion) and the poll-group
timeout covers the whole mapping step; a pathological template fails its poll and trips
the breaker rather than hanging the process.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from jinja2 import ChainableUndefined, TemplateError, Undefined
from jinja2.nativetypes import NativeEnvironment
from jinja2.sandbox import ImmutableSandboxedEnvironment

_EXPR_MARKERS = re.compile(r"\{\{|\{%")


class ExprError(ValueError):
    """Compile or render failure, with the source expression in the message."""


class _SandboxedNativeEnvironment(ImmutableSandboxedEnvironment, NativeEnvironment):
    """Sandbox rules (attribute/call/mutation policy) + native-typed rendering."""


def _is_number(value: Any) -> bool:  # noqa: ANN401
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return True
    if isinstance(value, str):
        try:
            float(value)
        except ValueError:
            return False
        return True
    return False


def _regex_search(value: Any, pattern: str) -> bool:  # noqa: ANN401
    return re.search(pattern, str(value)) is not None


def _utcnow(days: float = 0, hours: float = 0) -> str:
    """Now, shifted, as ISO 8601 UTC to the second — for a request's date window
    (``start: "{{ utcnow(days=-1) }}"``) and for comparing against ISO timestamps."""
    at = datetime.now(UTC) + timedelta(days=days, hours=hours)
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


_DURATION = re.compile(r"(?:(\d+)[.:])?(\d+):(\d{1,2}):(\d{1,2})(?:\.\d+)?|(\d+):(\d{1,2})")


def _seconds(value: Any) -> int | None:  # noqa: ANN401
    """A clock-style duration as seconds: ``0:12:34`` (SABnzbd), ``1:02:03:04`` (days first)
    and ``1.02:03:04`` (.NET, as the *arr APIs write ``timeleft``), or ``12:34``. Anything
    else is None, never a guess."""
    if not isinstance(value, str):
        return None
    m = _DURATION.fullmatch(value.strip())
    if m is None:
        return None
    if m.group(5) is not None:
        return int(m.group(5)) * 60 + int(m.group(6))
    days = int(m.group(1) or 0)
    return days * 86_400 + int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4))


def new_environment() -> _SandboxedNativeEnvironment:
    env = _SandboxedNativeEnvironment(undefined=ChainableUndefined, autoescape=False)
    env.globals["utcnow"] = _utcnow
    env.filters["seconds"] = _seconds
    env.filters["is_number"] = _is_number
    env.tests["number"] = _is_number
    env.filters["regex_search"] = _regex_search
    return env


class Expr:
    """One compiled expression. A plain string without ``{{``/``{%`` is a constant."""

    __slots__ = ("_const", "_template", "source")

    def __init__(self, env: _SandboxedNativeEnvironment, source: str) -> None:
        self.source = source
        self._template = None
        self._const: Any = None
        if _EXPR_MARKERS.search(source) is None:
            self._const = source
            return
        try:
            self._template = env.from_string(source)
        except TemplateError as exc:
            msg = f"cannot compile {source!r}: {exc}"
            raise ExprError(msg) from exc

    @property
    def is_constant(self) -> bool:
        return self._template is None

    def render(self, **ctx: Any) -> Any:  # noqa: ANN401
        """Native-typed result. Undefined → None; errors carry the source expression."""
        if self._template is None:
            return self._const
        try:
            # NativeTemplate.render returns native objects; the base stub says str.
            value: Any = self._template.render(**ctx)
        except TemplateError as exc:
            msg = f"{self.source!r}: {exc}"
            raise ExprError(msg) from exc
        except (OverflowError, TypeError, ValueError, ZeroDivisionError, LookupError) as exc:
            msg = f"{self.source!r}: {type(exc).__name__}: {exc}"
            raise ExprError(msg) from exc
        return None if isinstance(value, Undefined) else value

    def render_str(self, **ctx: Any) -> str:  # noqa: ANN401
        """String result; None becomes ``""``. Native rendering turns ``"42"`` into an int,
        so string-typed fields (uid, name, links) always pass through here."""
        value = self.render(**ctx)
        return "" if value is None else str(value)

    def render_bool(self, **ctx: Any) -> bool:  # noqa: ANN401
        value = self.render(**ctx)
        if isinstance(value, str):
            return value.strip().lower() not in ("", "false", "0", "no", "none")
        return bool(value)
