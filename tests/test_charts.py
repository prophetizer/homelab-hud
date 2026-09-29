# SPDX-License-Identifier: Apache-2.0
"""Charts and heatmaps from HUD's own history: every series on one time axis with gaps
kept as gaps, the busiest resources of a selection, and weekday-by-hour folding in the
instance's timezone."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, insert

from hud.collector import LiveCache
from hud.config.schemas.board import ChartSource
from hud.main import create_app
from hud.models import State, Unit
from hud.store import StorePaths, create_store_engine, upgrade_all
from hud.store.tables import samples, series
from hud.widgets import WidgetEngine
from hud.widgets.series import bucket_axis, bucketed, heat
from tests.conftest import sign_in_admin
from tests.test_api_health import _env
from tests.test_api_providers import BOARD, DEMO, THINGS
from tests.test_widgets_engine import T0, _board, board, metric, res

NOW = int(T0.timestamp())


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Engine]:
    paths = StorePaths(tmp_path / "data")
    upgrade_all(paths)
    eng = create_store_engine(paths)
    yield eng
    eng.dispose()


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    env = _env(tmp_path)
    (env.config_dir / "providers").mkdir(parents=True)
    (env.config_dir / "providers" / "demo.yaml").write_text(DEMO)
    with respx.mock(assert_all_called=False) as router:
        router.get("http://demo.lab/things").mock(return_value=httpx.Response(200, json=THINGS))
        with TestClient(create_app(env)) as c:
            sign_in_admin(c)
            assert c.post("/api/v1/providers/demo/reload").status_code == 200
            yield c


def _store(db: Engine, uid: str, name: str, points: list[tuple[int, float]]) -> None:
    with db.begin() as conn:
        sid = conn.execute(
            insert(series).values(
                provider=uid.split(":", 1)[0],
                resource_uid=uid,
                metric=name,
                unit="bps",
                first_seen=points[0][0],
                last_seen=points[-1][0],
            )
        ).inserted_primary_key[0]
        conn.execute(insert(samples), [{"series_id": sid, "ts": t, "value": v} for t, v in points])


def test_buckets_average_and_keep_gaps() -> None:
    assert bucket_axis(0, 100, 4) == [0, 25, 50, 75]
    # Two samples in the first bucket, none in the second, one in the last.
    assert bucketed([(1, 10.0), (20, 30.0), (99, 5.0)], 0, 100, 4) == [20.0, None, None, 5.0]


def test_heat_folds_by_weekday_and_hour_in_the_timezone() -> None:
    # 2026-09-28 is a Monday. 03:00 UTC is 22:00 on Sunday in Chicago.
    ts = int(datetime(2026, 9, 28, 3, 0, tzinfo=UTC).timestamp())
    grid = heat([(ts, 4.0), (ts + 60, 8.0)], ZoneInfo("America/Chicago"))
    assert grid[6][22] == 6.0  # Sunday, 22:00: the mean
    assert heat([(ts, 4.0), (ts + 60, 8.0)], ZoneInfo("America/Chicago"), "max")[6][22] == 8.0
    assert sum(v is not None for row in grid for v in row) == 1


@pytest.mark.parametrize(
    "source",
    [
        {},  # neither
        {"series": [{"resource": "a:b:c", "metric": "m"}], "metric": "m", "select": {}},  # both
        {"select": {"kind": "host"}},  # select without metric
        {"series": [{"resource": "a:b:c", "metric": "m"}], "range": "90d"},  # too long
    ],
)
def test_chart_source_takes_one_way_to_name_its_series(source: dict) -> None:
    with pytest.raises(ValidationError):
        ChartSource.model_validate(source)


NET = """
- id: net
  type: chart
  title: WAN
  grid: { col: 1, row: 1, w: 4, h: 2 }
  source:
    series:
      - { metric: rx_bps, resource: "glances:interface:eth0", label: Down }
      - { metric: tx_bps, resource: "glances:interface:eth0", label: Up }
    range: 1h
  display:
    kind: area
    stacked: true
    thresholds: [{ gte: 900000000, state: warn }]
"""


async def test_chart_series_share_one_axis(config_dir: Path, db: Engine) -> None:
    uid = "glances:interface:eth0"
    _store(db, uid, "rx_bps", [(NOW - 3000 + 60 * i, 1e6 * i) for i in range(20)])
    cache = LiveCache()
    cache.apply(
        "glances",
        "network",
        [res(uid)],
        [metric(uid, "rx_bps", 5e8, Unit.BPS), metric(uid, "tx_bps", 1e9, Unit.BPS)],
    )
    doc = board(config_dir, _board(NET))
    (w,) = (await WidgetEngine(cache, db).resolve_board(doc, T0)).widgets
    d = w.data
    assert d["range"] == "1h" and d["ranges"] == ["1h", "6h", "24h", "7d"]
    assert (d["kind"], d["stacked"], d["unit"]) == ("area", True, "bps")
    down, up = d["series"]
    assert len(d["x"]) == len(down["values"]) == len(up["values"])
    assert down["label"] == "Down" and down["max"] == 1.9e7
    assert up["values"] == [None] * len(d["x"])  # no stored history: all gap, no zeros
    assert w.state is State.DEGRADED  # tx at 1e9 crosses the warn line
    assert w.icon is not None  # one provider: its icon


async def test_a_chart_can_be_re_resolved_over_another_range(config_dir: Path, db: Engine) -> None:
    cache = LiveCache()
    doc = board(config_dir, _board(NET))
    engine = WidgetEngine(cache, db)
    (w,) = doc.spec.widgets
    resolved = await engine.resolve(w, T0, chart_range="7d")
    assert resolved.data["range"] == "7d"
    assert resolved.error == "no readings yet for any series"


BUSIEST = """
- id: busy
  type: chart
  grid: { col: 1, row: 1, w: 2 }
  source:
    select: { provider: docker, kind: container }
    metric: cpu_pct
    limit: 2
"""


async def test_a_selection_charts_its_busiest_resources(config_dir: Path) -> None:
    cache = LiveCache()
    names = {"a": 5.0, "b": 50.0, "c": 20.0}
    cache.apply(
        "docker",
        "collect",
        [res(f"docker:container:{n}") for n in names],
        [metric(f"docker:container:{n}", "cpu_pct", v, Unit.PCT) for n, v in names.items()],
    )
    doc = board(config_dir, _board(BUSIEST))
    engine = WidgetEngine(cache, None)
    (w,) = (await engine.resolve_board(doc, T0)).widgets
    assert [s["uid"] for s in w.data["series"]] == ["docker:container:b", "docker:container:c"]
    assert engine.referenced_uids(doc) == {"docker:container:b", "docker:container:c"}


HEAT = """
- id: plays
  type: heatmap
  grid: { col: 1, row: 1, w: 2 }
  source: { resource: "plex:activity:main", metric: streams }
  display: { range: 7d, agg: max }
"""


async def test_heatmap_reads_a_week_in_the_timezone(config_dir: Path, db: Engine) -> None:
    uid = "plex:activity:main"
    _store(db, uid, "streams", [(NOW - 3600, 2.0), (NOW - 3540, 3.0)])
    cache = LiveCache()
    cache.apply("plex", "activity", [res(uid)], [metric(uid, "streams", 1.0)])
    doc = board(config_dir, _board(HEAT))
    engine = WidgetEngine(cache, db, timezone=lambda: "America/Chicago")
    (w,) = (await engine.resolve_board(doc, T0)).widgets
    assert (w.data["min"], w.data["max"], w.data["samples"]) == (3.0, 3.0, 2)
    assert len(w.data["grid"]) == 7 and len(w.data["grid"][0]) == 24
    assert w.error is None and w.state is State.UP


async def test_a_heatmap_without_its_resource_says_so(config_dir: Path) -> None:
    doc = board(config_dir, _board(HEAT))
    (w,) = (await WidgetEngine(LiveCache(), None).resolve_board(doc, T0)).widgets
    assert w.state is State.UNKNOWN and "plex:activity:main" in (w.error or "")


def test_list_stats_resources_count_as_shown(config_dir: Path) -> None:
    """A list's stats strip exposes those resources, as a hero's stats do (RBAC scope)."""
    doc = board(
        config_dir,
        _board("""
- id: q
  type: list
  grid: { col: 1, row: 1 }
  source: { select: { kind: nothing } }
  display:
    stats: [{ resource: "sabnzbd:service:main", metric: speed_bps }]
"""),
    )
    assert WidgetEngine(LiveCache(), None).referenced_uids(doc) == {"sabnzbd:service:main"}


def test_widget_route_takes_a_range_and_is_scoped_like_its_board(client: TestClient) -> None:
    config_dir = Path(client.app.state.env.config_dir)  # type: ignore[attr-defined]
    (config_dir / "boards").mkdir(exist_ok=True)
    (config_dir / "boards" / "demo.yaml").write_text(BOARD)
    client.app.state.config.load()  # type: ignore[attr-defined]
    r = client.get("/api/v1/boards/demo/widgets/all", params={"range": "7d"})
    assert r.status_code == 200 and r.json()["id"] == "all"  # not a chart: range ignored
    assert client.get("/api/v1/boards/demo/widgets/all?range=90d").status_code == 422
    assert client.get("/api/v1/boards/demo/widgets/all?range=soon").status_code == 422
    assert client.get("/api/v1/boards/demo/widgets/nope").status_code == 404
    assert client.get("/api/v1/boards/nope/widgets/all").status_code == 404
