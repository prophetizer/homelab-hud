# SPDX-License-Identifier: Apache-2.0
"""Read-side API: resources, events, providers, reload — through the real lifespan."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from hud.main import create_app
from tests.conftest import sign_in_admin
from tests.test_api_health import _env

DEMO = """\
apiVersion: hud/v1
kind: Provider
metadata:
  name: demo
  labels: { category: test }
spec:
  transport: { base_url: http://demo.lab }
  defaults: { interval: 1h, jitter: 0s }
  resources:
    - name: things
      request: { path: /things }
      map:
        uid: "demo:thing:{{ item.name }}"
        kind: thing
        name: "{{ item.name }}"
        state: "{{ item.status }}"
      metrics:
        - { name: load, value: "{{ item.load }}", unit: pct }
"""

THINGS = [
    {"name": "alpha", "status": "up", "load": 12.5},
    {"name": "beta", "status": "down", "load": 0},
]


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get("http://demo.lab/things").mock(return_value=httpx.Response(200, json=THINGS))
        yield router


def things(mock: respx.MockRouter, response: httpx.Response) -> None:
    mock.get("http://demo.lab/things").mock(return_value=response)


@pytest.fixture
def client(tmp_path: Path, mock: respx.MockRouter) -> Iterator[TestClient]:
    env = _env(tmp_path)
    (env.config_dir / "providers").mkdir(parents=True)
    (env.config_dir / "providers" / "demo.yaml").write_text(DEMO)
    with TestClient(create_app(env)) as c:
        sign_in_admin(c)
        # Deterministic: reload polls synchronously, whatever the scheduler is doing.
        r = c.post("/api/v1/providers/demo/reload")
        assert r.status_code == 200, r.text
        assert r.json()["polled"] == {"things": True}
        yield c


def test_list_and_filter_resources(client: TestClient) -> None:
    body = client.get("/api/v1/resources").json()
    assert [r["uid"] for r in body["resources"]] == ["demo:thing:alpha", "demo:thing:beta"]
    assert body["generation"] >= 1
    assert all(r["stale"] is False for r in body["resources"])

    def names(**params: object) -> list[str]:
        body = client.get("/api/v1/resources", params=params).json()
        return [r["name"] for r in body["resources"]]

    assert names(state="down") == ["beta"]
    assert names(kind="nope") == []
    assert names(provider=["demo", "x"]) == ["alpha", "beta"]
    assert names(label="category:test") == ["alpha", "beta"]
    assert names(label="category:media") == []
    assert client.get("/api/v1/resources", params={"label": "nocolon"}).status_code == 422
    assert client.get("/api/v1/resources", params={"state": "sideways"}).status_code == 422


def test_resource_detail_with_metrics(client: TestClient) -> None:
    body = client.get("/api/v1/resources/demo:thing:alpha").json()
    assert body["resource"]["name"] == "alpha"
    assert body["error"] is None
    (m,) = body["metrics"]
    assert (m["name"], m["value"], m["unit"]) == ("load", 12.5, "pct")
    assert body["events"] == []
    assert client.get("/api/v1/resources/demo:thing:nope").status_code == 404


def test_failure_shows_stale_with_error_then_events(
    client: TestClient, mock: respx.MockRouter
) -> None:
    things(mock, httpx.Response(503, text="maintenance"))
    r = client.post("/api/v1/providers/demo/reload").json()
    assert r["polled"] == {"things": False}
    assert r["provider"]["status"] == "degraded"
    assert r["provider"]["last_error"] == "GET /things: HTTP 503 Service Unavailable"
    detail = client.get("/api/v1/resources/demo:thing:alpha").json()
    assert detail["resource"]["stale"] is True
    assert detail["resource"]["state"] == "up"  # last-known-good
    assert detail["error"] == "GET /things: HTTP 503 Service Unavailable"
    # Recovery with a state change produces an event visible on both endpoints.
    things(mock, httpx.Response(200, json=[{**THINGS[0], "status": "degraded"}, THINGS[1]]))
    assert client.post("/api/v1/providers/demo/reload").json()["provider"]["status"] == "ok"
    events = client.get("/api/v1/events").json()["events"]
    assert [(e["type"], e["severity"]) for e in events] == [("state_change", "warn")]
    assert events[0]["message"] == "alpha: up → degraded"

    def count(**params: object) -> int:
        return len(client.get("/api/v1/events", params=params).json()["events"])

    assert count(severity="error") == 0
    assert count(provider="other") == 0
    assert count(provider="demo", limit=1) == 1
    detail = client.get("/api/v1/resources/demo:thing:alpha").json()
    assert detail["events"][0]["type"] == "state_change"


def test_providers_endpoints(client: TestClient) -> None:
    body = client.get("/api/v1/providers").json()
    (p,) = body["providers"]
    assert p["name"] == "demo" and p["tier"] == "declarative" and p["status"] == "ok"
    assert p["groups"] == ["things"] and p["resource_count"] == 2
    assert p["labels"] == {"category": "test"}
    assert client.get("/api/v1/providers/demo").json()["name"] == "demo"
    assert client.get("/api/v1/providers/nope").status_code == 404
    assert client.post("/api/v1/providers/nope/reload").status_code == 404
    health = client.get("/api/v1/health").json()
    assert health["providers"][0]["status"] == "ok"


BOARD = """\
apiVersion: hud/v1
kind: Board
metadata: { name: demo, title: Demo Board }
spec:
  widgets:
    - id: alpha
      type: resource
      grid: { col: 1, row: 1 }
      source: { resource: demo:thing:alpha }
    - id: all
      type: list
      grid: { col: 2, row: 1 }
      source: { select: { provider: demo } }
    - id: later
      type: chart
      grid: { col: 3, row: 1 }
"""


def test_boards_endpoints(client: TestClient) -> None:
    config_dir = Path(client.app.state.env.config_dir)  # type: ignore[attr-defined]
    (config_dir / "boards").mkdir()
    (config_dir / "boards" / "demo.yaml").write_text(BOARD)
    assert client.get("/api/v1/boards").json() == {"boards": []}
    # Hot reload: the board appears without a restart once the watcher picks it up.
    client.app.state.config.load()  # type: ignore[attr-defined]
    (summary,) = client.get("/api/v1/boards").json()["boards"]
    assert summary == {
        "name": "demo",
        "title": "Demo Board",
        "icon": None,
        "widgets": 3,
        "unsupported": 1,
        "apps": 0,
        "state": "down",
        "down": 1,
    }
    body = client.get("/api/v1/boards/demo").json()
    assert body["title"] == "Demo Board" and body["generation"] >= 1
    w = {x["id"]: x for x in body["widgets"]}
    assert w["alpha"]["state"] == "up" and w["alpha"]["data"]["resource"]["name"] == "alpha"
    assert w["all"]["state"] == "down" and w["all"]["data"]["total"] == 2
    assert w["later"]["error"] == "widget type 'chart' arrives in Phase 2"
    assert client.get("/api/v1/boards/nope").status_code == 404


def test_index_carries_frame_src_allowlist(client: TestClient, tmp_path: Path) -> None:
    config_dir = Path(client.app.state.env.config_dir)  # type: ignore[attr-defined]
    (config_dir / "boards").mkdir(exist_ok=True)
    (config_dir / "boards" / "embeds.yaml").write_text(
        "apiVersion: hud/v1\nkind: Board\nmetadata: {name: embeds}\nspec:\n  widgets:\n"
        "    - {id: a, type: embed, grid: {col: 1, row: 1}, source: {url: 'https://grafana.lab/d/x'}}\n"
        "    - {id: b, type: embed, grid: {col: 2, row: 1}, source: {url: 'http://portainer.lab:9000/'}}\n"
    )
    client.app.state.config.load()  # type: ignore[attr-defined]
    r = client.get("/boards/embeds")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-security-policy"] == (
        "frame-src 'self' http://portainer.lab:9000 https://grafana.lab"
    )
