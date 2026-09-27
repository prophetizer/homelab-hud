# SPDX-License-Identifier: Apache-2.0
"""Uptime from availability spans (PLAN.md §6.3; §12 Phase 2 slice 1).

Spans are clipped to a window and summed per state. Up, degraded and paused count as up
(the service is answering); down counts against. Unknown spans — and time no span covers
at all, when HUD was not observing — are *excluded* from the ratio by default and reported
alongside, so HUD's own blind spots never read as a service's downtime.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo

from sqlalchemy import Engine, func, select

from hud.store.tables import availability as avail_t

RANGES = {"24h": 86_400, "7d": 7 * 86_400, "30d": 30 * 86_400, "90d": 90 * 86_400}
UP_STATES = ("up", "degraded", "paused")
STATES = (*UP_STATES, "down", "unknown")


@dataclass(frozen=True)
class Span:
    state: str
    start: int
    end: int
    approximate: bool
    open: bool = False  # still running: its end is "now"


def read_spans(
    engine: Engine, uids: Sequence[str], frm: int, to: int, now: int
) -> dict[str, list[Span]]:
    """Every span of these resources that overlaps [frm, to); an open span ends now."""
    out: dict[str, list[Span]] = {uid: [] for uid in uids}
    end = func.coalesce(avail_t.c.ended_at, now)
    with engine.connect() as conn:
        for i in range(0, len(uids), 500):
            chunk = list(uids[i : i + 500])
            rows = conn.execute(
                select(
                    avail_t.c.resource_uid,
                    avail_t.c.state,
                    avail_t.c.started_at,
                    end,
                    avail_t.c.approximate,
                    avail_t.c.ended_at.is_(None),
                )
                .where(avail_t.c.resource_uid.in_(chunk))
                .where(avail_t.c.started_at < to)
                .where(end > frm)
                .order_by(avail_t.c.resource_uid, avail_t.c.started_at)
            )
            for uid, state, start, stop, approx, is_open in rows:
                out[uid].append(Span(state, int(start), int(stop), bool(approx), bool(is_open)))
    return out


def tally(spans: Iterable[Span], frm: int, to: int) -> dict[str, int]:
    """Seconds per state inside [frm, to), plus ``not_observed`` for uncovered time."""
    t = dict.fromkeys(STATES, 0)
    for s in spans:
        overlap = min(s.end, to) - max(s.start, frm)
        if overlap > 0:
            t[s.state if s.state in t else "unknown"] += overlap
    t["not_observed"] = max(0, (to - frm) - sum(t[k] for k in STATES))
    return t


def ratio(t: dict[str, int], *, exclude_unknown: bool = True) -> float | None:
    """Uptime in percent, or None when nothing was observed. With exclude_unknown off,
    unknown and not-observed time count as not up (the stricter reading)."""
    up = sum(t[s] for s in UP_STATES)
    down = t["down"] + (0 if exclude_unknown else t["unknown"] + t["not_observed"])
    return None if up + down == 0 else up / (up + down) * 100


def cells(spans: Sequence[Span], frm: int, to: int, n: int) -> list[dict[str, int]]:
    """The window in n equal buckets, each a tally, with its start as ``t``."""
    width = (to - frm) / n
    out = []
    for i in range(n):
        b0, b1 = int(frm + i * width), int(frm + (i + 1) * width)
        out.append({"t": b0, **tally(spans, b0, b1)})
    return out


def day_cells(
    spans: Sequence[Span], days: int, tz: tzinfo, now: datetime
) -> list[dict[str, object]]:
    """One cell per calendar day in ``tz`` — midnight to midnight, DST-safe by building each
    day from its date — ending today, clipped at now so today is not "not observed"."""
    today = now.astimezone(tz).date()
    to = int(now.timestamp())
    out: list[dict[str, object]] = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        start = int(datetime.combine(d, time.min, tz).timestamp())
        end = min(int(datetime.combine(d + timedelta(days=1), time.min, tz).timestamp()), to)
        t = tally(spans, start, end)
        out.append(
            {"t": start, "date": d.isoformat(), "weekday": d.weekday(), **t, "pct": ratio(t)}
        )
    return out


__all__ = ["RANGES", "Span", "cells", "day_cells", "ratio", "read_spans", "tally"]
