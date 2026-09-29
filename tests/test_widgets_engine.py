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
from hud.config.schemas import BoardDocument, ListWidget
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
      type: report
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

    assert (
        w["later"].state is None and w["later"].error == "widget type 'report' arrives in Phase 2"
    )


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
    # Real since Phase 2 slice 1; without a metrics store it says so instead of guessing.
    assert w["plex-uptime"].error == "uptime needs the metrics store"
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


GROUPED = """\
apiVersion: hud/v1
kind: Board
metadata: {name: links}
spec:
  widgets:
    - id: media-links
      type: list
      grid: {col: 1, row: 1}
      source:
        select: {provider: docker, attrs: {homepage.group: Media Links}}
        sort: [name]
      display: {fields: [state, attrs.homepage.name]}
"""


async def test_list_filters_on_resource_attrs(config_dir: Path) -> None:
    """Homepage's link groups, reproduced: a list per homepage.group. `label` could not do
    it — it matches the provider's labels, and every container shares one provider."""
    cache = LiveCache()
    rows = [
        res("docker:container:scryer", homepage={"group": "Media Links", "name": "Scryer"}),
        res("docker:container:agregarr", homepage={"group": "Media Links", "name": "Agregarr"}),
        res("docker:container:ntfy", homepage={"group": "Infrastructure Links", "name": "ntfy"}),
        res("docker:container:plain"),  # no homepage labels at all
    ]
    cache.apply("docker", "collect", rows, [])
    engine = WidgetEngine(cache, None)
    doc = board(config_dir, GROUPED)
    resolved = await engine.resolve_board(doc, T0)
    (w,) = resolved.widgets
    assert [i["name"] for i in w.data["items"]] == ["agregarr", "scryer"]
    assert [i["fields"][1]["value"] for i in w.data["items"]] == ["Agregarr", "Scryer"]
    # RBAC's resource scope sees the same selection (a viewer of this board gets these two).
    assert engine.referenced_uids(doc) == {"docker:container:agregarr", "docker:container:scryer"}


async def test_list_rows_can_be_titled_by_a_field(config_dir: Path) -> None:
    """Links rows read "agregarr" above "Agregarr · Plex collections curator": the container
    name as title, the display name repeated underneath. display.title picks the field."""
    cache = LiveCache()
    cache.apply(
        "docker",
        "collect",
        [
            res("docker:container:agregarr", homepage={"group": "Media Links", "name": "Agregarr"}),
            res("docker:container:plain", homepage={"group": "Media Links"}),  # no name label
        ],
        [],
    )
    doc = board(
        config_dir,
        GROUPED.replace(
            "display: {fields: [state, attrs.homepage.name]}",
            "display: {title: attrs.homepage.name, fields: [state, attrs.homepage.name]}",
        ),
    )
    (w,) = (await WidgetEngine(cache, None).resolve_board(doc, T0)).widgets
    titles = {i["name"]: i["title"] for i in w.data["items"]}
    assert titles == {"agregarr": "Agregarr", "plain": "plain"}  # falls back to the name


def _board(widget: str) -> str:
    return (
        "apiVersion: hud/v1\nkind: Board\nmetadata: { name: b }\nspec:\n  widgets:\n"
        + "\n".join(f"    {line}" for line in widget.strip().splitlines())
        + "\n"
    )


async def test_bars_draw_one_metric_per_core_in_natural_order(config_dir: Path) -> None:
    """The per-core CPU tile: cpu2 before cpu10, each bar coloured by its own value, the
    headline the mean. Colour here is status (invariant 9): up, busy, hot."""
    cache = LiveCache()
    cores = {"cpu0": 12.0, "cpu10": 95.0, "cpu2": 80.0, "cpu1": 40.0}
    cache.apply(
        "glances",
        "percpu",
        [res(f"glances:core:{n}") for n in cores],
        [metric(f"glances:core:{n}", "cpu_pct", v, Unit.PCT) for n, v in cores.items()],
    )
    doc = board(
        config_dir,
        _board("""
- id: cores
  type: bars
  grid: { col: 1, row: 1 }
  source: { select: { provider: glances, kind: core }, sort: [name], metric: cpu_pct }
  display:
    layout: columns
    summary: mean
    thresholds: [{ gte: 75, state: warn }, { gte: 90, state: error }]
"""),
    )
    (w,) = (await WidgetEngine(cache, None).resolve_board(doc, T0)).widgets
    assert w.error is None
    assert [b["title"] for b in w.data["bars"]] == ["cpu0", "cpu1", "cpu2", "cpu10"]
    assert [b["state"] for b in w.data["bars"]] == ["up", "up", "degraded", "down"]
    assert w.data["summary"] == {"kind": "mean", "value": 56.75, "state": "up"}
    assert (w.data["unit"], w.data["max"], w.data["layout"]) == ("pct", 100.0, "columns")
    assert w.state is State.UP  # a hot core colours its bar, not the whole tile


async def test_bars_rank_the_busiest_with_missing_values_last(config_dir: Path) -> None:
    """Top containers by CPU. A container with no stats yet must not float to the top of a
    descending sort (it did: None keys sorted first under reverse=True)."""
    cache = LiveCache()
    cpu = {"web": 30.0, "db": 250.0, "cache": 5.0}
    cache.apply(
        "docker",
        "g",
        [res(f"docker:container:{n}") for n in [*cpu, "new"]]
        + [res("docker:container:dead", State.DOWN)],
        [metric(f"docker:container:{n}", "cpu_pct", v, Unit.PCT) for n, v in cpu.items()]
        + [metric("docker:container:dead", "cpu_pct", 0.0, Unit.PCT)],
    )
    doc = board(
        config_dir,
        _board("""
- id: busiest
  type: bars
  grid: { col: 1, row: 1 }
  source:
    select: { provider: docker }
    sort: ["-metric.cpu_pct"]
    limit: 5
    metric: cpu_pct
"""),
    )
    engine = WidgetEngine(cache, None)
    (w,) = (await engine.resolve_board(doc, T0)).widgets
    assert [b["title"] for b in w.data["bars"]] == ["db", "web", "cache", "dead", "new"]
    assert w.data["bars"][-1]["value"] is None
    assert w.data["bars"][3]["state"] == "down"  # a down container stays down
    assert w.data["max"] == 250.0  # over 100 % on a multi-core host: scale to the peak
    assert (w.data["total"], w.state) == (5, State.DOWN)
    assert "docker:container:new" in engine.referenced_uids(doc)


async def test_metric_total_shows_the_share_and_thresholds_judge_it(config_dir: Path) -> None:
    """Memory as "70.6 / 128 GiB": thresholds of 80/90 mean percent of the total, which
    compared against raw bytes would always be crossed."""
    cache = LiveCache()
    uid = "glances:memory:main"
    gib = 1024**3
    cache.apply(
        "glances",
        "memory",
        [res(uid)],
        [
            metric(uid, "used_bytes", 115 * gib, Unit.BYTES),
            metric(uid, "total_bytes", 128 * gib, Unit.BYTES),
        ],
    )
    doc = board(
        config_dir,
        _board(f"""
- id: mem
  type: metric
  grid: {{ col: 1, row: 1 }}
  source: {{ resource: "{uid}", metric: used_bytes }}
  display:
    total: {{ metric: total_bytes }}
    style: gauge
    thresholds: [{{ gte: 80, state: warn }}, {{ gte: 95, state: error }}]
"""),
    )
    (w,) = (await WidgetEngine(cache, None).resolve_board(doc, T0)).widgets
    assert w.data["total"] == {"value": 128 * gib, "unit": "bytes", "pct": 115 / 128 * 100}
    assert w.state is State.DEGRADED  # 89.8 % of the total: warn, not error
    assert w.data["style"] == "gauge"  # the SPA draws the share as a dial


async def test_list_rows_carry_a_usage_bar(config_dir: Path) -> None:
    cache = LiveCache()
    cache.apply(
        "glances",
        "fs",
        [res("glances:filesystem:/data"), res("glances:filesystem:/new")],
        [metric("glances:filesystem:/data", "used_pct", 47.1, Unit.PCT)],
    )
    doc = board(
        config_dir,
        _board("""
- id: disks
  type: list
  grid: { col: 1, row: 1 }
  source: { select: { kind: filesystem }, sort: ["-attrs.missing", name] }
  display: { fields: [metric.used_pct], bar: metric.used_pct }
"""),
    )
    (w,) = (await WidgetEngine(cache, None).resolve_board(doc, T0)).widgets
    assert [(i["name"], i["bar"]) for i in w.data["items"]] == [("/data", 47.1), ("/new", None)]
    assert w.data["layout"] == "rows"  # the default; `grid` draws the status wall


def test_metric_sort_keys_validate(config_dir: Path) -> None:
    ok = BoardDocument.model_validate(
        {
            "apiVersion": "hud/v1",
            "kind": "Board",
            "metadata": {"name": "b"},
            "spec": {
                "widgets": [
                    {
                        "id": "l",
                        "type": "list",
                        "grid": {"col": 1, "row": 1},
                        "source": {"sort": ["-metric.cpu_pct"]},
                    }
                ]
            },
        }
    )
    (w,) = ok.spec.widgets
    assert isinstance(w, ListWidget)
    assert w.source.sort == ["-metric.cpu_pct"]
    with pytest.raises(ValueError, match="unsupported sort key"):
        BoardDocument.model_validate(
            {
                "apiVersion": "hud/v1",
                "kind": "Board",
                "metadata": {"name": "b"},
                "spec": {
                    "widgets": [
                        {
                            "id": "l",
                            "type": "list",
                            "grid": {"col": 1, "row": 1},
                            "source": {"sort": ["metric."]},
                        }
                    ]
                },
            }
        )


async def test_card_rows_carry_an_icon_only_when_it_is_servable(config_dir: Path) -> None:
    """Homepage labels hold dashboard-icons names, mdi names, or paths into Homepage's own
    folder. Only the first two can be fetched; the rest get a letter badge (icon None)."""
    cache = LiveCache()
    cache.apply(
        "docker",
        "g",
        [
            res("docker:container:sonarr", homepage={"icon": "Sonarr.png"}),
            res("docker:container:glance", homepage={"icon": "mdi-view-dashboard"}),
            res("docker:container:picker", homepage={"icon": "/icons/theme-picker.svg"}),
            res("docker:container:plain"),
        ],
        [],
    )
    doc = board(
        config_dir,
        _board("""
- id: apps
  type: list
  grid: { col: 1, row: 1 }
  source: { select: { provider: docker }, sort: [name] }
  display: { layout: cards, icon: attrs.homepage.icon }
"""),
    )
    (w,) = (await WidgetEngine(cache, None).resolve_board(doc, T0)).widgets
    assert w.data["layout"] == "cards"
    assert {i["name"]: i["icon"] for i in w.data["items"]} == {
        "glance": "mdi-view-dashboard",
        "picker": None,
        "plain": None,
        "sonarr": "sonarr.png",
    }


async def test_tiles_carry_their_single_providers_icon(config_dir: Path) -> None:
    """A tile fed by one provider shows its icon; a mixed list shows none; widget.icon
    overrides, and "none" hides it."""
    cache = LiveCache()
    cache.apply(
        "glances", "g", [res("glances:cpu:main")], [metric("glances:cpu:main", "cpu_pct", 5)]
    )
    cache.apply("radarr", "g", [res("radarr:download:a")], [])
    cache.apply("sonarr", "g", [res("sonarr:download:b")], [])
    doc = board(
        config_dir,
        _board("""
- id: cpu
  type: metric
  grid: { col: 1, row: 1 }
  source: { resource: "glances:cpu:main", metric: cpu_pct }
- id: queue
  type: list
  grid: { col: 2, row: 1 }
  source: { select: { kind: download } }
- id: arr
  type: list
  grid: { col: 3, row: 1 }
  source: { select: { provider: [radarr] } }
- id: custom
  type: metric
  icon: "mdi:chip"
  grid: { col: 4, row: 1 }
  source: { resource: "glances:cpu:main", metric: cpu_pct }
- id: hidden
  type: metric
  icon: none
  grid: { col: 1, row: 2 }
  source: { resource: "glances:cpu:main", metric: cpu_pct }
"""),
    )
    resolved = await WidgetEngine(cache, None).resolve_board(doc, T0)
    icons = {w.id: w.icon for w in resolved.widgets}
    assert icons == {
        "cpu": "glances",
        "queue": None,
        "arr": "radarr",
        "custom": "mdi-chip",
        "hidden": None,
    }
    # The summary bar: distinct resources on the board, by state.
    assert resolved.summary == {"up": 3}


async def test_hero_strip_reads_stats_from_other_resources(config_dir: Path) -> None:
    """The Home header: the host's name and fields, plus readings from its CPU, memory and
    uptime resources, each judged by its own thresholds; a missing one reads unknown."""
    cache = LiveCache()
    cache.apply(
        "glances",
        "g",
        [res("glances:host:main", os="Linux"), res("glances:cpu:main"), res("glances:uptime:main")],
        [
            metric("glances:cpu:main", "cpu_pct", 93.0, Unit.PCT),
            metric("glances:uptime:main", "uptime_seconds", 380871.0, Unit.SECONDS),
        ],
    )
    doc = board(
        config_dir,
        _board("""
- id: host
  type: resource
  grid: { col: 1, row: 1, w: 4 }
  source: { resource: "glances:host:main" }
  display:
    style: hero
    fields: [attrs.os]
    stats:
      - { resource: "glances:uptime:main", metric: uptime_seconds, label: Uptime }
      - resource: "glances:cpu:main"
        metric: cpu_pct
        label: CPU
        thresholds: [{ gte: 90, state: error }]
      - { resource: "glances:memory:main", metric: used_pct, label: Memory }
"""),
    )
    engine = WidgetEngine(cache, None)
    (w,) = (await engine.resolve_board(doc, T0)).widgets
    assert w.data["style"] == "hero"
    assert [(s["label"], s["value"], s["state"]) for s in w.data["stats"]] == [
        ("Uptime", 380871.0, "up"),
        ("CPU", 93.0, "down"),
        ("Memory", None, "unknown"),
    ]
    assert {"glances:cpu:main", "glances:uptime:main"} <= engine.referenced_uids(doc)


async def test_metric_delta_compares_with_the_stored_sample_nearest_the_window(
    config_dir: Path, db: Engine
) -> None:
    from sqlalchemy import insert  # noqa: PLC0415

    from hud.store.tables import samples, series  # noqa: PLC0415

    uid = "adguard-home:dns:main"
    now = int(T0.timestamp())
    with db.begin() as conn:
        sid = conn.execute(
            insert(series).values(
                provider="adguard-home",
                resource_uid=uid,
                metric="queries",
                unit="count",
                first_seen=now - 7200,
                last_seen=now,
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(samples),
            [
                {"series_id": sid, "ts": now - 3600 + d, "value": v}
                for d, v in [(-200, 90.0), (20, 100.0)]
            ],
        )
    cache = LiveCache()
    cache.apply("adguard-home", "g", [res(uid)], [metric(uid, "queries", 112.0)])

    def tile(delta: str) -> str:
        return _board(f"""
- id: q
  type: metric
  grid: {{ col: 1, row: 1 }}
  source: {{ resource: "{uid}", metric: queries }}
  display: {{ delta: {delta} }}
""")

    (w,) = (await WidgetEngine(cache, db).resolve_board(board(config_dir, tile("1h")), T0)).widgets
    assert w.data["delta"] == {"window": "1h", "then": 100.0, "change": 12.0, "pct": 12.0}
    # No sample near 24 h ago: no delta, not a guess.
    (w,) = (await WidgetEngine(cache, db).resolve_board(board(config_dir, tile("24h")), T0)).widgets
    assert w.data["delta"] is None


def test_board_summary_carries_its_worst_state_and_down_count(config_dir: Path) -> None:
    cache = LiveCache()
    cache.apply(
        "docker",
        "g",
        [
            res("docker:container:a"),
            res("docker:container:b", State.DOWN),
            res("docker:container:c", State.DOWN),
        ],
        [],
    )
    doc = board(
        config_dir,
        _board("""
- id: all
  type: list
  grid: { col: 1, row: 1 }
  source: { select: { provider: docker } }
"""),
    )
    s = WidgetEngine(cache, None).summary(doc)
    assert (s.state, s.down) == (State.DOWN, 2)


def test_expand_lifts_the_row_limit_and_lengthens_the_trend(config_dir: Path) -> None:
    doc = board(
        config_dir,
        _board("""
- id: top
  type: bars
  grid: { col: 1, row: 1 }
  source: { select: { provider: docker }, sort: ["-metric.cpu_pct"], limit: 8, metric: cpu_pct }
- id: cpu
  type: metric
  grid: { col: 2, row: 1 }
  source: { resource: "glances:cpu:main", metric: cpu_pct }
"""),
    )
    engine = WidgetEngine(LiveCache(), None)
    bars, cpu = doc.spec.widgets
    big_bars, big_cpu = engine.expand(bars), engine.expand(cpu)
    assert big_bars.source.limit is None and bars.source.limit == 8  # type: ignore[union-attr]
    assert big_cpu.display.sparkline.range == "24h"  # type: ignore[union-attr]
    assert cpu.display.sparkline is None  # type: ignore[union-attr]  # the board is untouched
