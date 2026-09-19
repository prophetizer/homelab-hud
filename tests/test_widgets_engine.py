# SPDX-License-Identifier: Apache-2.0
"""Widget engine: every Phase 1 type resolves from the cache; each degrades on its own."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import Engine

from hud.collector import LiveCache, Normalized, StoreWriter
from hud.config import ConfigManager
from hud.config.schemas import BoardDocument
from hud.models import Metric, Resource, State, Unit
from hud.store import StorePaths, create_store_engine, upgrade_all
from hud.widgets import WidgetEngine
from hud.widgets.samples import downsample
from tests.conftest import FIXTURES

T0 = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def res(uid: str, state: State = State.UP, stale: bool = False, **attrs: object) -> Resource:
    provider, kind, native = uid.split(":", 2)
    return Resource(
        uid=uid,
        provider=provider,
        kind=kind,
        name=native,
        state=state,
        attrs=dict(attrs),
        links={"ui": f"http://{native}.lab"},
        fetched_at=T0,
        stale=stale,
    )


def metric(uid: str, name: str, value: float, unit: Unit = Unit.COUNT, ts: datetime = T0) -> Metric:
    return Metric(resource_uid=uid, name=name, value=value, unit=unit, ts=ts)


def board(config_dir: Path, text: str) -> BoardDocument:
    (config_dir / "settings.yaml").write_text("apiVersion: hud/v1\nkind: Settings\n")
    (config_dir / "boards").mkdir(exist_ok=True)
    (config_dir / "boards" / "b.yaml").write_text(text)
    snap = ConfigManager(config_dir).load()
    (doc,) = [d.model for d in snap.documents if isinstance(d.model, BoardDocument)]
    return doc


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Engine]:
    paths = StorePaths(tmp_path / "data")
    upgrade_all(paths)
    eng = create_store_engine(paths)
    yield eng
    eng.dispose()


@pytest.fixture
def cache() -> LiveCache:
    c = LiveCache()
    c.register_provider("sonarr", {"category": "media"})
    c.register_provider("radarr", {"category": "media"})
    c.register_provider("docker", {})
    c.apply(
        "sonarr",
        "g",
        [res("sonarr:service:main", version="4.0.9")],
        [metric("sonarr:service:main", "queue_size", 120)],
    )
    c.apply("radarr", "g", [res("radarr:service:main", State.DOWN, version="5.1")], [])
    c.apply(
        "docker",
        "g",
        [res("docker:container:web", State.DEGRADED), res("docker:container:db", stale=True)],
        [metric("docker:container:web", "cpu_pct", 42.5, Unit.PCT)],
    )
    c.mark_stale("docker", "g", "HTTP 500")
    return c


BOARD = """\
apiVersion: hud/v1
kind: Board
metadata: { name: infra, title: Infra, icon: mdi:server }
spec:
  layout: { columns: { lg: 6 }, gap: 8 }
  widgets:
    - id: note
      type: static
      grid: { col: 1, row: 1 }
      display: { text: hello, links: [{ title: Docs, url: https://docs.lab }] }
    - id: sonarr
      type: resource
      title: Sonarr
      grid: { col: 2, row: 1 }
      source: { resource: sonarr:service:main }
      display: { fields: [name, state, attrs.version, metric.queue_size, links.ui, attrs.nope] }
    - id: ghost
      type: resource
      grid: { col: 3, row: 1 }
      source: { resource: nothing:here:x }
    - id: arr
      type: list
      grid: { col: 1, row: 2, w: 2 }
      source:
        select: { label: { category: media } }
        sort: ["-state_severity", name]
      display: { fields: [attrs.version], empty_text: none }
    - id: containers
      type: list
      grid: { col: 3, row: 2 }
      source: { select: { provider: docker, kind: container }, sort: [name], limit: 1 }
    - id: nothing
      type: list
      grid: { col: 4, row: 2 }
      source: { select: { kind: unicorn } }
    - id: queue
      type: metric
      grid: { col: 1, row: 3 }
      source: { metric: queue_size, resource: sonarr:service:main }
      display:
        thresholds: [{ gte: 50, state: warn }, { gte: 200, state: error }]
        sparkline: { range: 1h }
    - id: cpu
      type: metric
      grid: { col: 2, row: 3 }
      source: { metric: cpu_pct, resource: docker:container:web }
    - id: nometric
      type: metric
      grid: { col: 3, row: 3 }
      source: { metric: nope, resource: sonarr:service:main }
    - id: framed
      type: embed
      grid: { col: 1, row: 4, w: 4, h: 3 }
      source: { url: http://grafana.lab/ }
    - id: blocked
      type: embed
      grid: { col: 5, row: 4 }
      source: { url: http://portainer.lab/ }
      display: { fallback: new_tab }
    - id: later
      type: chart
      grid: { col: 6, row: 4 }
      source: { series: [] }
"""


@respx.mock
async def test_resolve_every_type(config_dir: Path, cache: LiveCache, db: Engine) -> None:
    respx.get("http://grafana.lab/").mock(return_value=httpx.Response(200))
    respx.get("http://portainer.lab/").mock(
        return_value=httpx.Response(200, headers={"X-Frame-Options": "DENY"})
    )
    # An hour of queue_size samples for the sparkline.
    writer = StoreWriter(db)
    for i in range(400):
        ts = T0 - timedelta(minutes=59) + timedelta(seconds=i * 9)
        sample = metric("sonarr:service:main", "queue_size", i, ts=ts)
        await writer("sonarr", "g", Normalized(metrics=[sample]), [])
    engine = WidgetEngine(cache, db)
    resolved = await engine.resolve_board(board(config_dir, BOARD), T0)
    assert (resolved.name, resolved.title, resolved.icon) == ("infra", "Infra", "mdi:server")
    assert resolved.layout.columns.lg == 6 and resolved.generation == cache.generation
    w = {x.id: x for x in resolved.widgets}
    assert list(w) == [x.id for x in board(config_dir, BOARD).spec.widgets]

    assert w["note"].state is None and w["note"].error is None
    assert w["note"].data == {
        "text": "hello",
        "links": [{"title": "Docs", "url": "https://docs.lab"}],
    }

    s = w["sonarr"]
    assert s.state is State.UP and not s.stale and s.title == "Sonarr"
    assert [(f["key"], f["value"]) for f in s.data["fields"]] == [
        ("name", "main"),
        ("state", "up"),
        ("attrs.version", "4.0.9"),
        ("metric.queue_size", 120.0),
        ("links.ui", "http://main.lab"),
        ("attrs.nope", None),
    ]
    assert s.data["fields"][3]["unit"] == "count" and s.data["fields"][3]["label"] == "queue size"
    assert s.data["resource"]["uid"] == "sonarr:service:main"

    g = w["ghost"]
    assert g.state is State.UNKNOWN and "no resource 'nothing:here:x'" in (g.error or "")

    arr = w["arr"]
    assert arr.state is State.DOWN  # worst item
    assert [i["uid"] for i in arr.data["items"]] == ["radarr:service:main", "sonarr:service:main"]
    assert arr.data["items"][0]["fields"] == [
        {"key": "attrs.version", "label": "version", "value": "5.1", "unit": None}
    ]
    assert arr.data["total"] == 2 and not arr.stale

    c = w["containers"]
    assert c.data["total"] == 2 and [i["name"] for i in c.data["items"]] == ["db"]
    assert c.stale is True and c.state is State.UP  # db is up but stale
    assert w["nothing"].state is State.UP and w["nothing"].data["items"] == []
    assert w["nothing"].data["empty_text"] == "Nothing to show"

    q = w["queue"]
    assert q.state is State.DEGRADED  # 120 ≥ 50 warn, < 200
    assert q.data["value"] == 120.0 and q.data["unit"] == "count"
    assert q.data["resource_name"] == "main"
    points = q.data["sparkline"]["points"]
    assert q.data["sparkline"]["range"] == "1h"
    assert 60 <= len(points) <= 120 and points[0][0] < points[-1][0]  # ~394 raw → bucketed
    assert points[-1][1] > points[0][1]

    cpu = w["cpu"]
    assert cpu.state is State.UP and cpu.stale is True and cpu.data["sparkline"] is None
    assert w["nometric"].state is State.UNKNOWN
    assert "no metric 'nope'" in (w["nometric"].error or "")

    assert w["framed"].state is State.UP and w["framed"].data["framing"]["allowed"] is True
    b = w["blocked"]
    assert b.state is State.DEGRADED and b.error == "cannot be framed: X-Frame-Options: DENY"
    assert b.data["fallback"] == "new_tab" and b.data["url"] == "http://portainer.lab/"

    assert w["later"].state is None and w["later"].error == "widget type 'chart' arrives in Phase 2"


async def test_plan_media_board_resolves_with_empty_cache(config_dir: Path) -> None:
    with respx.mock:
        respx.get("https://tautulli.lab.internal/").mock(side_effect=httpx.ConnectError("no dns"))
        doc = board(config_dir, (FIXTURES / "boards" / "media.yaml").read_text())
        resolved = await WidgetEngine(LiveCache(), None).resolve_board(doc, T0)
    w = {x.id: x for x in resolved.widgets}
    assert w["arr-health"].data["items"] == []
    assert w["arr-health"].data["empty_text"] == "No services discovered"
    assert w["queue-depth"].state is State.UNKNOWN
    assert "no resource" in (w["queue-depth"].error or "")
    assert w["plex-uptime"].error == "widget type 'uptime' arrives in Phase 2"
    assert w["tautulli"].state is State.UNKNOWN
    assert (w["tautulli"].error or "").startswith("unreachable")


async def test_resolver_exception_degrades_one_tile(config_dir: Path, cache: LiveCache) -> None:
    engine = WidgetEngine(cache, None)

    def boom(*_: object) -> object:
        raise RuntimeError("resolver bug")

    engine._static = boom  # type: ignore[method-assign]
    resolved = await engine.resolve_board(board(config_dir, BOARD), T0)
    w = {x.id: x for x in resolved.widgets}
    assert w["note"].error == "RuntimeError: resolver bug" and w["note"].state is State.UNKNOWN
    assert w["sonarr"].state is State.UP


def test_downsample_budget() -> None:
    pts = [(i, float(i)) for i in range(1000)]
    out = downsample(pts, 100)
    assert len(out) == 100 and out[0] == (9, 4.5) and out[-1][0] == 999
    assert downsample(pts[:50], 100) == pts[:50]
