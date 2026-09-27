# SPDX-License-Identifier: Apache-2.0
"""Availability spans (PLAN §6.1, §6.3): recorded from the live cache, rebuilt once from
logged events, and read back as uptime that never counts HUD's blind spots as downtime."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert, select

from hud.collector import LiveCache
from hud.collector.availability import AvailabilityRecorder
from hud.models import Resource, State
from hud.store import StorePaths, create_store_engine, upgrade_all
from hud.store.tables import availability, events, series
from hud.widgets import WidgetEngine
from hud.widgets.uptime import Span, cells, ratio, tally
from tests.test_widgets_engine import _board, board

T = 1_790_000_000  # a fixed "now", unix seconds


class Clock:
    def __init__(self, t: int = T) -> None:
        self.t = t

    def __call__(self) -> float:
        return float(self.t)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Engine]:
    paths = StorePaths(tmp_path / "data")
    upgrade_all(paths)
    eng = create_store_engine(paths)
    yield eng
    eng.dispose()


def res(uid: str, state: State = State.UP, stale: bool = False) -> Resource:
    provider, kind, native = uid.split(":", 2)
    return Resource(
        uid=uid,
        provider=provider,
        kind=kind,
        name=native,
        state=state,
        fetched_at=datetime.fromtimestamp(T, UTC),
        stale=stale,
    )


def spans(db: Engine) -> list[tuple[str, str, int, int | None, int | None, int]]:
    c = availability.c
    with db.connect() as conn:
        rows = conn.execute(
            select(
                c.resource_uid, c.state, c.started_at, c.ended_at, c.confirmed_at, c.approximate
            ).order_by(c.resource_uid, c.started_at)
        ).all()
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------- recorder


def test_reconcile_opens_confirms_and_closes_spans(db: Engine) -> None:
    cache, clock = LiveCache(), Clock()
    rec = AvailabilityRecorder(cache, db, clock)
    cache.apply("sonarr", "g", [res("sonarr:service:main"), res("sonarr:download:a")], [])
    rec.reconcile()
    clock.t += 30
    rec.reconcile()  # unchanged: confirmed, not reopened
    assert spans(db) == [
        ("sonarr:download:a", "up", T, None, T + 30, 0),
        ("sonarr:service:main", "up", T, None, T + 30, 0),
    ]
    clock.t += 30
    cache.apply("sonarr", "g", [res("sonarr:service:main", State.DOWN)], [])  # a gone too
    rec.reconcile()
    assert spans(db) == [
        ("sonarr:download:a", "up", T, T + 30, T + 30, 0),  # closed where last confirmed
        ("sonarr:service:main", "up", T, T + 60, T + 30, 0),
        ("sonarr:service:main", "down", T + 60, None, T + 60, 0),
    ]


def test_a_failing_provider_is_down_for_its_service_unknown_for_the_rest(db: Engine) -> None:
    """Mike's rule: the service not answering is downtime; its children are unobserved."""
    cache = LiveCache()
    rec = AvailabilityRecorder(cache, db, Clock())
    cache.apply("sonarr", "g", [res("sonarr:service:main"), res("sonarr:download:a")], [])
    cache.mark_stale("sonarr", "g", "timed out")
    rec.reconcile()
    assert {(uid, state) for uid, state, *_ in spans(db)} == {
        ("sonarr:service:main", "down"),
        ("sonarr:download:a", "unknown"),
    }


def test_restart_closes_dangling_spans_where_last_confirmed(db: Engine) -> None:
    """A redeploy is HUD's outage: the gap is left uncovered (not observed), not down."""
    cache, clock = LiveCache(), Clock()
    cache.apply("plex", "g", [res("plex:service:main")], [])
    AvailabilityRecorder(cache, db, clock).reconcile()
    clock.t += 3600  # the process dies without closing anything
    assert AvailabilityRecorder(LiveCache(), db, clock).close_dangling() == 1
    assert spans(db) == [("plex:service:main", "up", T, T, T, 0)]


def test_backfill_rebuilds_approximate_spans_once(db: Engine) -> None:
    with db.begin() as conn:
        conn.execute(
            insert(series),
            [
                {
                    "provider": "plex",
                    "resource_uid": "plex:service:main",
                    "metric": "x",
                    "unit": "count",
                    "first_seen": T - 1000,
                    "last_seen": T,
                }
            ],
        )
        conn.execute(
            insert(events),
            [
                {
                    "resource_uid": "plex:service:main",
                    "type": "state_change",
                    "severity": "error",
                    "message": "lab-plex: up → down",
                    "ts": T - 600,
                },
                {
                    "resource_uid": "plex:service:main",
                    "type": "state_change",
                    "severity": "info",
                    "message": "lab-plex: down → up",
                    "ts": T - 300,
                },
                {
                    "resource_uid": "plex:service:main",
                    "type": "state_change",
                    "severity": "info",
                    "message": "not a transition",
                    "ts": T - 200,
                },
            ],
        )
    rec = AvailabilityRecorder(LiveCache(), db, Clock())
    assert rec.backfill() == 3
    assert spans(db) == [
        ("plex:service:main", "up", T - 1000, T - 600, T - 600, 1),  # from the first series
        ("plex:service:main", "down", T - 600, T - 300, T - 300, 1),
        ("plex:service:main", "up", T - 300, T, T, 1),
    ]
    assert rec.backfill() == 0  # never again once there is history


# ---------------------------------------------------------------- uptime maths


def test_tally_clips_and_reports_what_it_did_not_see() -> None:
    s = [
        Span("up", 0, 600, False),
        Span("down", 600, 900, False),
        Span("unknown", 900, 1000, False),
    ]
    t = tally(s, 300, 1200)  # 300 s of it before, 200 s after the spans
    assert (t["up"], t["down"], t["unknown"], t["not_observed"]) == (300, 300, 100, 200)
    assert ratio(t) == 50.0  # unknown and not-observed are left out (§6.3)
    assert ratio(t, exclude_unknown=False) == pytest.approx(300 / 900 * 100)
    assert ratio(tally([], 0, 100)) is None  # nothing observed: no number, not 0 %


def test_degraded_and_paused_count_as_up() -> None:
    s = [Span("degraded", 0, 50, False), Span("paused", 50, 100, False)]
    assert ratio(tally(s, 0, 100)) == 100.0


def test_cells_split_the_window_evenly() -> None:
    out = cells([Span("up", 0, 100, False)], 0, 100, 4)
    assert [c["t"] for c in out] == [0, 25, 50, 75]
    assert all(c["up"] == 25 for c in out)


# ---------------------------------------------------------------- widget


async def test_uptime_widget_reads_history_for_its_resources(config_dir: Path, db: Engine) -> None:
    cache = LiveCache()
    cache.apply("plex", "g", [res("plex:service:main")], [])
    with db.begin() as conn:
        conn.execute(
            insert(availability),
            [
                {
                    "resource_uid": "plex:service:main",
                    "state": "up",
                    "started_at": T - 7200,
                    "ended_at": T - 3600,
                    "confirmed_at": T - 3600,
                    "approximate": 1,
                },
                {
                    "resource_uid": "plex:service:main",
                    "state": "down",
                    "started_at": T - 3600,
                    "ended_at": None,
                    "confirmed_at": T,
                    "approximate": 0,
                },
            ],
        )
    doc = board(
        config_dir,
        _board("""
- id: plex-uptime
  type: uptime
  grid: { col: 1, row: 1 }
  source: { resource: "plex:service:main" }
  display: { range: 24h, buckets: 24, sla_range: 30d }
"""),
    )
    now = datetime.fromtimestamp(T, UTC)
    (w,) = (await WidgetEngine(cache, db).resolve_board(doc, now)).widgets
    assert w.error is None
    (row,) = w.data["rows"]
    assert len(row["cells"]) == 24 and w.data["bucket_seconds"] == 3600
    assert row["cells"][-1]["down"] == 3600 and row["cells"][-2]["up"] == 3600
    assert row["sla"] == 50.0  # an hour up, an hour down; the rest of 30 days unobserved
    assert row["approximate"] is True
    assert w.icon == "plex"


async def test_list_rows_carry_a_24h_strip_when_asked(config_dir: Path, db: Engine) -> None:
    cache = LiveCache()
    cache.apply("docker", "g", [res("docker:container:web"), res("docker:container:db")], [])
    with db.begin() as conn:
        conn.execute(
            insert(availability),
            [
                {
                    "resource_uid": "docker:container:web",
                    "state": "down",
                    "started_at": T - 1800,
                    "ended_at": None,
                    "confirmed_at": T,
                    "approximate": 0,
                },
            ],
        )
    doc = board(
        config_dir,
        _board("""
- id: apps
  type: list
  grid: { col: 1, row: 1 }
  source: { select: { provider: docker }, sort: [name] }
  display: { layout: cards, uptime: true }
"""),
    )
    (w,) = (
        await WidgetEngine(cache, db).resolve_board(doc, datetime.fromtimestamp(T, UTC))
    ).widgets
    rows = {i["name"]: i["uptime"] for i in w.data["items"]}
    assert len(rows["web"]["cells"]) == 24 and rows["web"]["cells"][-1]["down"] == 1800
    assert rows["web"]["sla"] == 0.0  # observed only while down
    assert rows["db"]["sla"] is None  # never observed: no number


# ---------------------------------------------------------------- status, incidents, calendar


def _cache_with(*resources: Resource) -> LiveCache:
    cache = LiveCache()
    by_provider: dict[str, list[Resource]] = {}
    for r in resources:
        by_provider.setdefault(r.provider, []).append(r)
    for p, rs in by_provider.items():
        cache.apply(p, "g", rs, [])
    return cache


def _one(widget_yaml: str, config_dir: Path) -> object:
    return board(config_dir, _board(widget_yaml))


async def test_status_headline_names_the_worst_news(config_dir: Path) -> None:
    doc = _one(
        """
- id: s
  type: status
  grid: { col: 1, row: 1 }
  source: { select: { kind: service } }
""",
        config_dir,
    )
    ok = _cache_with(res("plex:service:main"), res("sonarr:service:main"))
    (w,) = (await WidgetEngine(ok, None).resolve_board(doc, datetime.fromtimestamp(T, UTC))).widgets  # type: ignore[arg-type]
    assert w.data["headline"] == "All systems operational" and w.data["problems"] == []
    partial = _cache_with(
        res("plex:service:main"), res("sonarr:service:main"), res("radarr:service:main", State.DOWN)
    )
    (w,) = (
        await WidgetEngine(partial, None).resolve_board(doc, datetime.fromtimestamp(T, UTC))
    ).widgets  # type: ignore[arg-type]
    assert w.data["headline"] == "Partial outage"
    assert [p["title"] for p in w.data["problems"]] == ["main"]
    assert w.data["counts"] == {"up": 2, "down": 1}


async def test_incidents_list_what_went_down_and_for_how_long(config_dir: Path, db: Engine) -> None:
    cache = _cache_with(res("plex:service:main"), res("sonarr:service:main", State.DEGRADED))
    with db.begin() as conn:
        conn.execute(
            insert(availability),
            [
                {
                    "resource_uid": "plex:service:main",
                    "state": "down",
                    "started_at": T - 7200,
                    "ended_at": T - 6600,
                    "confirmed_at": T - 6600,
                    "approximate": 0,
                },
                {
                    "resource_uid": "plex:service:main",
                    "state": "up",
                    "started_at": T - 6600,
                    "ended_at": None,
                    "confirmed_at": T,
                    "approximate": 0,
                },
                {
                    "resource_uid": "sonarr:service:main",
                    "state": "degraded",
                    "started_at": T - 600,
                    "ended_at": None,
                    "confirmed_at": T,
                    "approximate": 0,
                },
            ],
        )
    doc = _one(
        """
- id: i
  type: incidents
  grid: { col: 1, row: 1 }
  source: { select: { kind: service } }
  display: { range: 24h }
""",
        config_dir,
    )
    (w,) = (
        await WidgetEngine(cache, db).resolve_board(doc, datetime.fromtimestamp(T, UTC))
    ).widgets  # type: ignore[arg-type]
    got = [(i["uid"], i["state"], i["end"], i["seconds"]) for i in w.data["incidents"]]
    assert got == [
        ("sonarr:service:main", "degraded", None, 600),  # newest first, still going
        ("plex:service:main", "down", T - 6600, 600),
    ]
    assert w.state is State.UP  # nothing is down right now


def test_calendar_days_start_at_local_midnight() -> None:
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    from hud.widgets.uptime import day_cells  # noqa: PLC0415

    tz = ZoneInfo("America/Chicago")
    now = datetime(2026, 9, 27, 15, 0, tzinfo=tz)
    days = day_cells([Span("up", 0, 2_000_000_000, False)], 3, tz, now)
    assert [d["date"] for d in days] == ["2026-09-25", "2026-09-26", "2026-09-27"]
    assert datetime.fromtimestamp(int(str(days[0]["t"])), tz).hour == 0
    assert days[-1]["up"] == 15 * 3600  # today stops at now, not midnight
    assert days[0]["pct"] == 100.0


async def test_select_missing_finds_everything_not_in_a_group(config_dir: Path) -> None:
    grouped = res("docker:container:a")
    grouped = grouped.model_copy(update={"attrs": {"homepage": {"group": "Media"}}})
    cache = _cache_with(grouped, res("docker:container:b"), res("docker:container:c"))
    doc = _one(
        """
- id: rest
  type: list
  grid: { col: 1, row: 1 }
  source: { select: { provider: docker, missing: [homepage.group] }, sort: [name] }
""",
        config_dir,
    )
    (w,) = (
        await WidgetEngine(cache, None).resolve_board(doc, datetime.fromtimestamp(T, UTC))
    ).widgets  # type: ignore[arg-type]
    assert [i["name"] for i in w.data["items"]] == ["b", "c"]


async def test_list_rows_carry_a_trend_of_their_metric(config_dir: Path, db: Engine) -> None:
    from hud.store.tables import samples  # noqa: PLC0415

    cache = _cache_with(res("web:endpoint:sonarr"))
    with db.begin() as conn:
        sid = conn.execute(
            insert(series).values(
                provider="web",
                resource_uid="web:endpoint:sonarr",
                metric="response_seconds",
                unit="seconds",
                first_seen=T - 3600,
                last_seen=T,
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(samples),
            [{"series_id": sid, "ts": T - 600 + 60 * i, "value": 0.1 * i} for i in range(10)],
        )
    doc = _one(
        """
- id: web
  type: list
  grid: { col: 1, row: 1 }
  source: { select: { provider: web } }
  display: { trend: response_seconds }
""",
        config_dir,
    )
    (w,) = (
        await WidgetEngine(cache, db).resolve_board(doc, datetime.fromtimestamp(T, UTC))
    ).widgets  # type: ignore[arg-type]
    (row,) = w.data["items"]
    assert len(row["trend"]) == 10 and row["trend"][-1][1] == pytest.approx(0.9)
