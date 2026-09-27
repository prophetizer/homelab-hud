# SPDX-License-Identifier: Apache-2.0
"""Availability recorder (PLAN.md §6.1, §6.3; §12 Phase 2 slice 1).

Availability is kept as transition spans, not samples: one row per stretch of a single
state, the open one per resource marked by ``ended_at IS NULL``. Every 30 s the recorder
reconciles the live cache against the open spans — a changed state closes one span and
opens the next, an unchanged one is stamped ``confirmed_at``, a vanished resource's span
is closed where it was last confirmed.

What a state *means* here:

- A resource whose provider is failing (stale) is **unknown** — HUD cannot see it — except
  the provider's own ``service`` resource, which is **down**: the service is not answering.
- Time with no span at all (HUD not running, a provider not yet polled) is *not observed*.
  Uptime excludes it, so a redeploy is never a service's downtime.

On start the recorder closes spans left open by the previous process at their last
confirmation, and — once, into an empty table — rebuilds spans from the ``state_change``
events logged before it existed, flagged ``approximate``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from typing import Any

from sqlalchemy import Engine, func, insert, select, update

from hud.collector.cache import LiveCache
from hud.models import Resource, State
from hud.store.tables import availability as avail_t
from hud.store.tables import events as events_t
from hud.store.tables import series as series_t

log = logging.getLogger(__name__)

RECONCILE_SECONDS = 30.0
_STATES = {s.value for s in State}
_ARROW = " → "


def effective_state(r: Resource) -> tuple[str, str | None]:
    """(state, reason) as availability records it."""
    if r.stale:
        if r.kind == "service":
            return State.DOWN.value, "provider unreachable"
        return State.UNKNOWN.value, "provider failing"
    return r.state.value, None


class AvailabilityRecorder:
    def __init__(
        self, cache: LiveCache, engine: Engine, clock: Callable[[], float] = time.time
    ) -> None:
        self.cache = cache
        self.engine = engine
        self.clock = clock

    # ------------------------------------------------------------------ lifecycle

    async def run(self) -> None:
        """Close what the last process left open, backfill once, then reconcile forever."""
        closed = await asyncio.to_thread(self.close_dangling)
        if closed:
            log.info("availability: closed %d span(s) left open by the previous run", closed)
        rebuilt = await asyncio.to_thread(self.backfill)
        if rebuilt:
            log.info("availability: rebuilt %d approximate span(s) from logged events", rebuilt)
        while True:
            await asyncio.sleep(RECONCILE_SECONDS)
            try:
                await asyncio.to_thread(self.reconcile)
            except Exception:
                log.exception("availability: reconcile failed; retrying next cycle")

    # ------------------------------------------------------------------ steps

    def close_dangling(self) -> int:
        """Spans still open from a previous process end where they were last confirmed; the
        gap until this process observes again is left uncovered — not observed."""
        with self.engine.begin() as conn:
            result = conn.execute(
                update(avail_t)
                .where(avail_t.c.ended_at.is_(None))
                .values(ended_at=func.coalesce(avail_t.c.confirmed_at, avail_t.c.started_at))
            )
            return int(result.rowcount or 0)

    def reconcile(self) -> None:
        now = int(self.clock())
        current = {r.uid: effective_state(r) for r in self.cache.resources()}
        with self.engine.begin() as conn:
            open_spans = {
                uid: (span_id, state)
                for span_id, uid, state in conn.execute(
                    select(avail_t.c.id, avail_t.c.resource_uid, avail_t.c.state).where(
                        avail_t.c.ended_at.is_(None)
                    )
                )
            }
            confirm: list[int] = []
            to_open: list[dict[str, Any]] = []
            to_close: list[int] = []
            for uid, (state, reason) in current.items():
                span = open_spans.pop(uid, None)
                if span is not None and span[1] == state:
                    confirm.append(span[0])
                    continue
                if span is not None:
                    to_close.append(span[0])
                to_open.append(
                    {
                        "resource_uid": uid,
                        "state": state,
                        "started_at": now,
                        "confirmed_at": now,
                        "reason": reason,
                    }
                )
            if to_close:
                conn.execute(update(avail_t).where(avail_t.c.id.in_(to_close)).values(ended_at=now))
            for chunk in _chunks(confirm, 500):
                conn.execute(
                    update(avail_t).where(avail_t.c.id.in_(chunk)).values(confirmed_at=now)
                )
            # Gone from the cache (container removed, provider dropped): stop where last seen.
            gone = [span_id for span_id, _ in open_spans.values()]
            for chunk in _chunks(gone, 500):
                conn.execute(
                    update(avail_t)
                    .where(avail_t.c.id.in_(chunk))
                    .values(ended_at=func.coalesce(avail_t.c.confirmed_at, avail_t.c.started_at))
                )
            if to_open:
                conn.execute(insert(avail_t), to_open)

    def backfill(self) -> int:
        """Once, into an empty table: spans from the ``state_change`` events logged since HUD
        first ran ("name: up → down"). Restarts in that period cannot be told apart, so every
        rebuilt span is ``approximate``; each resource's first span starts at its earliest
        series, when it has one."""
        now = int(self.clock())
        with self.engine.begin() as conn:
            if conn.execute(select(func.count()).select_from(avail_t)).scalar_one():
                return 0
            changes: dict[str, list[tuple[int, str, str]]] = {}
            rows = conn.execute(
                select(events_t.c.resource_uid, events_t.c.message, events_t.c.ts)
                .where(events_t.c.type == "state_change")
                .order_by(events_t.c.resource_uid, events_t.c.ts)
            )
            for uid, message, ts in rows:
                parsed = _parse_change(message)
                if parsed is not None:
                    changes.setdefault(uid, []).append((int(ts), *parsed))
            if not changes:
                return 0
            first_seen = {
                str(uid): int(ts)
                for uid, ts in conn.execute(
                    select(series_t.c.resource_uid, func.min(series_t.c.first_seen))
                    .where(series_t.c.resource_uid.in_(list(changes)))
                    .group_by(series_t.c.resource_uid)
                )
            }
            spans = list(_rebuild(changes, first_seen, now))
            if spans:
                conn.execute(insert(avail_t), spans)
            return len(spans)


def _parse_change(message: str) -> tuple[str, str] | None:
    """A logged transition, e.g. 'lab-plex: up → down', as ("up", "down"); else None."""
    _, _, tail = message.rpartition(": ")
    before, arrow, after = tail.partition(_ARROW)
    if not arrow or before not in _STATES or after not in _STATES:
        return None
    return before, after


def _rebuild(
    changes: dict[str, list[tuple[int, str, str]]], first_seen: dict[str, int], now: int
) -> Iterable[dict[str, Any]]:
    for uid, seq in changes.items():
        start = first_seen.get(uid)
        first_ts, first_before, _ = seq[0]
        if start is not None and start < first_ts:
            yield _span(uid, first_before, start, first_ts)
        for i, (ts, _, after) in enumerate(seq):
            end = seq[i + 1][0] if i + 1 < len(seq) else now
            if end > ts:
                yield _span(uid, after, ts, end)


def _span(uid: str, state: str, start: int, end: int) -> dict[str, Any]:
    return {
        "resource_uid": uid,
        "state": state,
        "started_at": start,
        "ended_at": end,
        "confirmed_at": end,
        "reason": "rebuilt from logged events",
        "approximate": 1,
    }


def _chunks(ids: list[int], size: int) -> Iterable[list[int]]:
    for i in range(0, len(ids), size):
        yield ids[i : i + size]


__all__ = ["RECONCILE_SECONDS", "AvailabilityRecorder", "effective_state"]
