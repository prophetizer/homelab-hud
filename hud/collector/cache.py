# SPDX-License-Identifier: Apache-2.0
"""Live State Cache (PLAN.md §4.1): the current value of every resource and metric.

* ``apply()`` installs one poll group's result. Resources the group produced last time but
  not this time are removed. State transitions become ``state_change`` events.
* ``mark_stale()`` is called when a poll fails: the last-known-good objects stay, flagged
  ``stale=True`` so every widget shows them as such (invariant 6 — never stale-looking-
  fresh). The next successful ``apply()`` clears the flag.
* Queries are cheap dict lookups. Everything runs on the event loop; there is no locking
  and none is needed as long as callers do not hold references across an ``await``.

Nothing here persists. The store writer (persist.py) is a separate consumer of the same
poll results.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from hud.models import Event, Metric, Resource, Severity, State

# Order used by list widgets' "-state_severity" sort: worst first.
STATE_SEVERITY: dict[State, int] = {
    State.DOWN: 4,
    State.DEGRADED: 3,
    State.UNKNOWN: 2,
    State.PAUSED: 1,
    State.UP: 0,
}

EVENT_RING = 2000


@dataclass(frozen=True)
class ResourceFilter:
    provider: Sequence[str] | None = None
    kind: Sequence[str] | None = None
    state: Sequence[State] | None = None
    labels: Mapping[str, str] | None = None  # all must match the resource's provider labels

    def matches(self, r: Resource, provider_labels: Mapping[str, str]) -> bool:
        if self.provider is not None and r.provider not in self.provider:
            return False
        if self.kind is not None and r.kind not in self.kind:
            return False
        if self.state is not None and r.state not in self.state:
            return False
        if self.labels is not None:
            return all(provider_labels.get(k) == v for k, v in self.labels.items())
        return True


@dataclass
class _GroupState:
    uids: set[str] = field(default_factory=set)
    error: str | None = None


class LiveCache:
    def __init__(self) -> None:
        self._resources: dict[str, Resource] = {}
        self._metrics: dict[tuple[str, str], Metric] = {}
        self._groups: dict[tuple[str, str], _GroupState] = {}
        self._provider_labels: dict[str, dict[str, str]] = {}
        self._events: deque[Event] = deque(maxlen=EVENT_RING)
        self.generation = 0  # bumps on every change; cheap "anything new?" for pollers

    # ------------------------------------------------------------------ providers

    def register_provider(self, name: str, labels: Mapping[str, str]) -> None:
        self._provider_labels[name] = dict(labels)

    def remove_provider(self, name: str, now: datetime | None = None) -> list[Event]:
        """Drop everything a provider produced (it was removed from config)."""
        now = now or datetime.now(UTC)
        events: list[Event] = []
        for key in [k for k in self._groups if k[0] == name]:
            events.extend(self._remove_uids(self._groups.pop(key).uids, now, "provider removed"))
        self._provider_labels.pop(name, None)
        self._touch()
        return events

    def provider_labels(self, name: str) -> Mapping[str, str]:
        return self._provider_labels.get(name, {})

    # ------------------------------------------------------------------ writes

    def apply(
        self,
        provider: str,
        group: str,
        resources: Iterable[Resource],
        metrics: Iterable[Metric],
        events: Iterable[Event] = (),
    ) -> list[Event]:
        """Install a successful poll. Returns the events it generated (state changes and
        removals) plus the provider's own, in order, already recorded in the ring."""
        key = (provider, group)
        state = self._groups.setdefault(key, _GroupState())
        state.error = None
        out: list[Event] = []
        seen: set[str] = set()
        for r in resources:
            fresh = r.model_copy(update={"stale": False}) if r.stale else r
            prev = self._resources.get(fresh.uid)
            if prev is not None and prev.state is not fresh.state:
                out.append(
                    Event(
                        resource_uid=fresh.uid,
                        type="state_change",
                        severity=_severity_for(fresh.state),
                        message=f"{fresh.name}: {prev.state.value} → {fresh.state.value}",
                        ts=fresh.fetched_at,
                    )
                )
            self._resources[fresh.uid] = fresh
            seen.add(fresh.uid)
        for m in metrics:
            if m.resource_uid in seen or m.resource_uid in self._resources:
                self._metrics[(m.resource_uid, m.name)] = m
        gone = state.uids - seen
        now = datetime.now(UTC)
        out.extend(self._remove_uids(gone, now, "no longer reported"))
        state.uids = seen
        out.extend(events)
        self._events.extend(out)
        self._touch()
        return out

    def mark_stale(self, provider: str, group: str, error: str) -> None:
        """A poll failed: keep last-known-good, flag it, remember why."""
        state = self._groups.setdefault((provider, group), _GroupState())
        state.error = error
        for uid in state.uids:
            r = self._resources.get(uid)
            if r is not None and not r.stale:
                self._resources[uid] = r.model_copy(update={"stale": True})
        self._touch()

    def group_error(self, provider: str, group: str) -> str | None:
        state = self._groups.get((provider, group))
        return state.error if state else None

    # ------------------------------------------------------------------ reads

    def resource(self, uid: str) -> Resource | None:
        return self._resources.get(uid)

    def resources(self, flt: ResourceFilter | None = None) -> list[Resource]:
        if flt is None:
            return list(self._resources.values())
        return [
            r for r in self._resources.values() if flt.matches(r, self.provider_labels(r.provider))
        ]

    def metric(self, uid: str, name: str) -> Metric | None:
        return self._metrics.get((uid, name))

    def metrics_for(self, uid: str) -> list[Metric]:
        return [m for (u, _), m in self._metrics.items() if u == uid]

    def events(self, limit: int = 100, *, uid: str | None = None) -> list[Event]:
        """Most recent first."""
        it = reversed(self._events)
        if uid is not None:
            it = (e for e in it if e.resource_uid == uid)
        out: list[Event] = []
        for e in it:
            out.append(e)
            if len(out) >= limit:
                break
        return out

    def provider_resource_count(self, provider: str) -> int:
        return sum(len(g.uids) for (p, _), g in self._groups.items() if p == provider)

    def __len__(self) -> int:
        return len(self._resources)

    # ------------------------------------------------------------------ internals

    def _remove_uids(self, uids: Iterable[str], now: datetime, why: str) -> list[Event]:
        events: list[Event] = []
        for uid in sorted(uids):
            r = self._resources.pop(uid, None)
            if r is None:
                continue
            for key in [k for k in self._metrics if k[0] == uid]:
                del self._metrics[key]
            events.append(
                Event(
                    resource_uid=uid,
                    type="removed",
                    severity=Severity.INFO,
                    message=f"{r.name}: {why}",
                    ts=now,
                )
            )
        return events

    def _touch(self) -> None:
        self.generation += 1


def _severity_for(state: State) -> Severity:
    if state is State.DOWN:
        return Severity.ERROR
    if state in (State.DEGRADED, State.UNKNOWN):
        return Severity.WARN
    return Severity.INFO
