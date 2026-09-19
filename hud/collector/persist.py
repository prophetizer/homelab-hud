# SPDX-License-Identifier: Apache-2.0
"""Store writer: raw samples and events into ``metrics.db`` (PLAN.md §6.1, R3).

A single writer serialises every poll group's output through one lock, and each poll
becomes exactly one transaction: upsert the touched ``series`` rows, batch-insert the
``samples`` (``OR REPLACE`` on the ``(series_id, ts)`` key: two samples in the same second
for one series collapse to the latest), append the ``events``, bump ``last_seen``.
Rollups, retention and availability spans are the Phase 2 worker's job; nothing here
reads, and nothing here ever blocks the event loop — the write runs in a thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import Connection, Engine, insert, select, update

from hud.collector.normalize import Normalized
from hud.models import Event, Metric
from hud.store.tables import events as events_t
from hud.store.tables import samples as samples_t
from hud.store.tables import series as series_t

log = logging.getLogger(__name__)


class StoreWriter:
    """Collector sink. ``await writer(provider, group, normalized, events)``."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._lock = asyncio.Lock()
        self._series_ids: dict[tuple[str, str], int] = {}
        self.samples_written = 0
        self.events_written = 0

    async def __call__(
        self, provider: str, group: str, normalized: Normalized, events: list[Event]
    ) -> None:
        if not normalized.metrics and not events:
            return
        async with self._lock:
            written = await asyncio.to_thread(self._write, provider, normalized.metrics, events)
        self.samples_written += written[0]
        self.events_written += written[1]

    # ------------------------------------------------------------------ sync side

    def _write(
        self, provider: str, metrics: Iterable[Metric], events: Iterable[Event]
    ) -> tuple[int, int]:
        sample_rows: list[dict[str, object]] = []
        touched: dict[int, int] = {}  # series id → newest sample ts
        event_rows: list[dict[str, object]] = []
        with self._engine.begin() as conn:
            for m in metrics:
                sid = self._series_id(conn, provider, m)
                ts = _unix(m.ts)
                sample_rows.append({"series_id": sid, "ts": ts, "value": m.value})
                touched[sid] = max(touched.get(sid, 0), ts)
            if sample_rows:
                conn.execute(insert(samples_t).prefix_with("OR REPLACE"), sample_rows)
                for sid, ts in touched.items():
                    conn.execute(
                        update(series_t)
                        .where(series_t.c.id == sid, series_t.c.last_seen < ts)
                        .values(last_seen=ts)
                    )
            for e in events:
                event_rows.append(
                    {
                        "resource_uid": e.resource_uid,
                        "type": e.type,
                        "severity": e.severity.value,
                        "message": e.message,
                        "ts": _unix(e.ts),
                    }
                )
            if event_rows:
                conn.execute(insert(events_t), event_rows)
        return len(sample_rows), len(event_rows)

    def _series_id(self, conn: Connection, provider: str, m: Metric) -> int:
        key = (m.resource_uid, m.name)
        cached = self._series_ids.get(key)
        if cached is not None:
            return cached
        row = conn.execute(
            select(series_t.c.id, series_t.c.unit).where(
                series_t.c.resource_uid == m.resource_uid, series_t.c.metric == m.name
            )
        ).first()
        ts = _unix(m.ts)
        if row is None:
            inserted = conn.execute(
                insert(series_t).values(
                    provider=provider,
                    resource_uid=m.resource_uid,
                    metric=m.name,
                    unit=m.unit.value,
                    labels_json=json.dumps({}),
                    first_seen=ts,
                    last_seen=ts,
                )
            ).inserted_primary_key
            assert inserted is not None
            sid = int(inserted[0])
        else:
            sid = int(row.id)
            if row.unit != m.unit.value:
                # A metric's canonical unit should never change; if a provider's mapping
                # did, say so loudly and follow it rather than mixing units in one series.
                log.warning(
                    "series %s/%s changed unit %s → %s",
                    m.resource_uid,
                    m.name,
                    row.unit,
                    m.unit.value,
                )
                conn.execute(update(series_t).where(series_t.c.id == sid).values(unit=m.unit.value))
        self._series_ids[key] = int(sid)
        return int(sid)


def _unix(ts: datetime) -> int:
    return int(ts.timestamp())
