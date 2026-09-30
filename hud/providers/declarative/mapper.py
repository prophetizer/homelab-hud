# SPDX-License-Identifier: Apache-2.0
"""``map`` / ``metrics`` blocks → canonical objects (PLAN.md §5, §7.1).

This is the normalization boundary for Tier 1: uid derivation through :func:`make_uid`,
state coercion into :class:`State`, and unit conversion through :meth:`Metric.from_source`.
Everything downstream sees canonical objects only.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from hud.config.interpolate import MissingEnvVarError, interpolate_env
from hud.config.schemas.provider import MetricMap, ResourceMap
from hud.models import Metric, Resource, SourceUnit, State, make_uid, source_unit_from_alias
from hud.providers.declarative.expr import Expr, ExprError, new_environment
from hud.providers.errors import ProviderBuildError

_ENV = new_environment()


class ItemError(ValueError):
    """One item could not be mapped. The poll continues; the engine counts these."""


@dataclass(frozen=True)
class _MetricExprs:
    name: str
    value: Expr
    when: Expr | None
    unit: SourceUnit | None
    unit_from: Expr | None


class ResourceMapper:
    """Compiled form of one ``resources[]`` entry's ``map`` and ``metrics``."""

    def __init__(
        self,
        provider: str,
        spec: ResourceMap,
        metrics: list[MetricMap],
        env: Mapping[str, str],
        log: logging.Logger,
    ) -> None:
        self.provider = provider
        self.kind = spec.kind
        self.log = log
        self._warned: set[str] = set()
        try:
            self.uid = self._compile(spec.uid, env)
            self.uid_on_collision = (
                self._compile(spec.uid_on_collision, env) if spec.uid_on_collision else None
            )
            self.name = self._compile(spec.name, env)
            self.state = self._compile(spec.state, env)
            self.parent_uid = self._compile(spec.parent_uid, env) if spec.parent_uid else None
            self.attrs = {k: self._compile(v, env) for k, v in spec.attrs.items()}
            self.links = {k: self._compile(v, env) for k, v in spec.links.items()}
            self.metrics = [self._compile_metric(m, env) for m in metrics]
        except (ExprError, MissingEnvVarError) as exc:
            raise ProviderBuildError(provider, f"map: {exc}") from exc

    @staticmethod
    def _compile(source: str, env: Mapping[str, str]) -> Expr:
        return Expr(_ENV, interpolate_env(source, env))

    def _compile_metric(self, m: MetricMap, env: Mapping[str, str]) -> _MetricExprs:
        unit: SourceUnit | None = None
        if m.unit is not None:
            unit = source_unit_from_alias(m.unit)
            if unit is None:
                msg = f"metric {m.name!r}: unknown unit {m.unit!r}"
                raise ProviderBuildError(self.provider, msg)
        return _MetricExprs(
            name=m.name,
            value=self._compile(m.value, env),
            when=self._compile(m.when, env) if m.when else None,
            unit=unit,
            unit_from=self._compile(m.unit_from, env) if m.unit_from else None,
        )

    # ------------------------------------------------------------------ per item

    def map_item(
        self,
        item: Any,  # noqa: ANN401
        fetched_at: datetime,
        *,
        collision: bool = False,
    ) -> tuple[Resource, list[Metric]]:
        """One item as a resource and its metrics; ``collision`` maps it under
        ``uid_on_collision`` (the caller found its ``uid`` already taken)."""
        ctx = {"item": item}
        expr = self.uid_on_collision if collision and self.uid_on_collision else self.uid
        try:
            uid = expr.render_str(**ctx)
            prefix = f"{self.provider}:{self.kind}:"
            native_id = uid.removeprefix(prefix)
            if not uid.startswith(prefix):
                msg = f"uid {uid!r} lost its {prefix!r} prefix"
                raise ItemError(msg)
            resource = Resource(
                uid=make_uid(self.provider, self.kind, native_id),
                provider=self.provider,
                kind=self.kind,
                name=self.name.render_str(**ctx) or native_id,
                state=self._state(self.state.render(**ctx)),
                attrs={k: _plain(e.render(**ctx)) for k, e in self.attrs.items()},
                links=self._links(ctx),
                parent_uid=self._parent(ctx),
                fetched_at=fetched_at,
            )
        except (ExprError, ValueError) as exc:
            raise ItemError(str(exc)) from exc
        return resource, self._metrics(resource.uid, ctx, fetched_at)

    def _links(self, ctx: dict[str, Any]) -> dict[str, str]:
        rendered = ((k, e.render_str(**ctx)) for k, e in self.links.items())
        return {k: v for k, v in rendered if v}

    def _parent(self, ctx: dict[str, Any]) -> str | None:
        if self.parent_uid is None:
            return None
        return self.parent_uid.render_str(**ctx) or None

    def _state(self, value: Any) -> State:  # noqa: ANN401
        text = "" if value is None else str(value).strip().lower()
        try:
            return State(text)
        except ValueError:
            self._warn_once(
                f"state:{text}",
                "state %r is not one of %s; using 'unknown'",
                text,
                ", ".join(s.value for s in State),
            )
            return State.UNKNOWN

    def _metrics(self, uid: str, ctx: dict[str, Any], ts: datetime) -> list[Metric]:
        out: list[Metric] = []
        for m in self.metrics:
            try:
                if m.when is not None and not m.when.render_bool(**ctx):
                    continue
                raw = m.value.render(**ctx)
                if raw is None or isinstance(raw, bool):
                    continue
                value = float(raw)
                unit = m.unit
                if unit is None and m.unit_from is not None:
                    spelled = m.unit_from.render_str(**ctx)
                    unit = source_unit_from_alias(spelled)
                    if unit is None:
                        self._warn_once(
                            f"unit:{spelled}",
                            "unit %r is unknown; recording as dimensionless",
                            spelled,
                        )
                        unit = SourceUnit.NONE
                metric = Metric.from_source(
                    resource_uid=uid,
                    name=m.name,
                    value=value,
                    source_unit=unit or SourceUnit.NONE,
                    ts=ts,
                )
                out.append(metric)
            except (ExprError, ValueError, TypeError) as exc:
                key = f"metric:{m.name}:{exc}"
                self._warn_once(key, "metric %r on %s skipped: %s", m.name, uid, exc)
        return out

    def _warn_once(self, key: str, msg: str, *args: Any) -> None:  # noqa: ANN401
        if key not in self._warned:
            self._warned.add(key)
            self.log.warning(msg, *args)


def _plain(value: Any) -> Any:  # noqa: ANN401
    """Attrs must be JSON-serialisable; anything exotic is stringified."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return str(value)
