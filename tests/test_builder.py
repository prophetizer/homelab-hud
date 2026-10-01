# SPDX-License-Identifier: Apache-2.0
"""The widget builder (PLAN §8.5 Flow B): the catalog lists what HUD collects with metric
units; a draft previews against live data without being written; adding, editing and
removing a widget edit the board file in place — comments, key order and keys the builder
does not know survive — and are refused on a stale revision or without boards:edit."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hud.main import create_app
from hud.models import Unit
from tests.conftest import sign_in_admin
from tests.test_api_health import _env
from tests.test_widgets_engine import metric, res

BOARD = """\
# The media board — hand-written.
apiVersion: hud/v1
kind: Board
metadata: { name: media, title: Media }
spec:
  sections:
    - { id: now, title: Now }
  widgets:
    # Plex streams, top left.
    - id: streams
      type: metric
      title: Streams
      section: now
      grid: { col: 1, row: 1 }
      source: { resource: "tautulli:activity:main", metric: streams }
      display:
        mystery: kept  # a key the builder does not know
    - id: notes
      type: static
      grid: { col: 1, row: 1 }
      content: "Hello"
"""


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    env = _env(tmp_path)
    (env.config_dir / "boards").mkdir(parents=True)
    (env.config_dir / "boards" / "media.yaml").write_text(BOARD)
    with TestClient(create_app(env)) as c:
        c.app.state.cache.apply(  # type: ignore[attr-defined]
            "tautulli",
            "activity",
            [res("tautulli:activity:main")],
            [
                metric("tautulli:activity:main", "streams", 2),
                metric("tautulli:activity:main", "bandwidth_bps", 14e6, Unit.BPS),
            ],
        )
        yield c


def _file(client: TestClient) -> str:
    return (client.app.state.env.config_dir / "boards" / "media.yaml").read_text()  # type: ignore[attr-defined]


def _revision(client: TestClient) -> str:
    return client.get("/api/v1/boards/media").json()["revision"]


def test_the_catalog_lists_what_hud_collects_with_units(client: TestClient) -> None:
    assert client.get("/api/v1/builder/catalog").status_code == 401
    sign_in_admin(client)
    groups = {
        (g["provider"], g["kind"]): g
        for g in client.get("/api/v1/builder/catalog").json()["groups"]
    }
    g = groups[("tautulli", "activity")]
    assert g["count"] == 1 and g["resources"][0]["uid"] == "tautulli:activity:main"
    assert g["metrics"] == {"bandwidth_bps": "bps", "streams": "count"}


def test_a_draft_previews_against_live_data_and_is_written_nowhere(client: TestClient) -> None:
    sign_in_admin(client)
    before = _file(client)
    draft = {
        "type": "metric",
        "title": "Bandwidth",
        "source": {"resource": "tautulli:activity:main", "metric": "bandwidth_bps"},
    }
    r = client.post("/api/v1/boards/media/builder/preview", json={"widget": draft})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["value"] == 14e6
    assert _file(client) == before
    bad = client.post(
        "/api/v1/boards/media/builder/preview", json={"widget": {"type": "metric", "source": {}}}
    )
    assert bad.status_code == 422 and "source" in bad.json()["detail"]


def test_adding_places_the_widget_below_its_section_and_keeps_every_comment(
    client: TestClient,
) -> None:
    sign_in_admin(client)
    rev = _revision(client)
    draft = {
        "type": "metric",
        "title": "Streams",  # taken: the id becomes streams-2
        "section": "now",
        "grid": {"w": 2},
        "source": {"resource": "tautulli:activity:main", "metric": "streams"},
        "display": {"style": "gauge"},
    }
    r = client.post("/api/v1/boards/media/builder/widgets", json={"revision": rev, "widget": draft})
    assert r.status_code == 201, r.text
    ids = [w["id"] for w in r.json()["widgets"]]
    assert ids == ["streams", "notes", "streams-2"]
    added = next(w for w in r.json()["widgets"] if w["id"] == "streams-2")
    assert added["grid"] == {"col": 1, "row": 2, "w": 2, "h": 1} and added["section"] == "now"
    text = _file(client)
    assert "# The media board — hand-written." in text and "# Plex streams, top left." in text
    assert "mystery: kept  # a key the builder does not know" in text
    assert "grid: {col: 1, row: 2, w: 2}" in text
    # The same revision again is stale: the file has changed since.
    again = client.post(
        "/api/v1/boards/media/builder/widgets", json={"revision": rev, "widget": draft}
    )
    assert again.status_code == 409


def test_editing_merges_only_what_changed(client: TestClient) -> None:
    sign_in_admin(client)
    form = client.get("/api/v1/boards/media/builder/widgets/streams").json()
    assert form["widget"]["display"] == {"mystery": "kept"}
    changes = {"title": "Plex streams", "display": {"style": "gauge"}, "section": None}
    r = client.patch(
        "/api/v1/boards/media/builder/widgets/streams",
        json={"revision": form["revision"], "changes": changes},
    )
    assert r.status_code == 200, r.text
    text = _file(client)
    assert "title: Plex streams" in text and "section: now\n      grid" not in text
    assert "mystery: kept  # a key the builder does not know" in text and "style: gauge" in text
    assert "# Plex streams, top left." in text
    rev = _revision(client)
    moved = client.patch(
        "/api/v1/boards/media/builder/widgets/streams",
        json={"revision": rev, "changes": {"grid": {"col": 2}}},
    )
    assert moved.status_code == 422


def test_removing_deletes_one_entry_in_place(client: TestClient) -> None:
    sign_in_admin(client)
    r = client.delete(
        "/api/v1/boards/media/builder/widgets/notes", params={"revision": _revision(client)}
    )
    assert r.status_code == 200, r.text
    assert [w["id"] for w in r.json()["widgets"]] == ["streams"]
    text = _file(client)
    assert "notes" not in text and "# Plex streams, top left." in text and "mystery: kept" in text


def test_only_an_editor_may_build(client: TestClient) -> None:
    sign_in_admin(client)
    r = client.post(
        "/api/v1/auth/users",
        json={"username": "viewer", "password": "a-long-enough-password", "groups": ["household"]},
    )
    assert r.status_code == 201, r.text
    client.post("/api/v1/auth/logout")
    r = client.post(
        "/api/v1/auth/login", json={"username": "viewer", "password": "a-long-enough-password"}
    )
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert client.get("/api/v1/builder/catalog").status_code == 403
    draft = {"type": "static", "content": "x"}
    assert (
        client.post("/api/v1/boards/media/builder/preview", json={"widget": draft}).status_code
        == 403
    )
