# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/apps`` — workspace launchers derived from visible boards (PLAN.md §8.4)."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from hud.main import create_app
from tests.conftest import sign_in_admin
from tests.test_api_health import _env

BOARDS = {
    "media": """\
apiVersion: hud/v1
kind: Board
metadata: { name: media, title: Media }
spec:
  widgets:
    - id: tautulli
      type: embed
      title: Tautulli
      grid: { col: 1, row: 1 }
      source: { url: "http://tautulli.lab/", sandbox: relaxed }
      display: { open_in: workspace, fallback: new_tab }
    - id: inline-grafana
      type: embed
      grid: { col: 2, row: 1 }
      source: { url: "http://grafana.lab/" }
      display: { open_in: inline }
""",
    "infra": """\
apiVersion: hud/v1
kind: Board
metadata: { name: infra, title: Infra }
spec:
  widgets:
    - id: portainer
      type: embed
      grid: { col: 1, row: 1 }
      source: { url: "http://portainer.lab/" }
      display: { open_in: workspace }
""",
}

RBAC = """\
apiVersion: hud/v1
kind: RBAC
spec:
  groups:
    admins: { permissions: ["*"] }
    household: { permissions: ["boards:view:media"] }
  defaults: { unmatched_group: guests }
"""


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get("http://tautulli.lab/").mock(return_value=httpx.Response(200))
        router.get("http://grafana.lab/").mock(return_value=httpx.Response(200))
        router.get("http://portainer.lab/").mock(
            return_value=httpx.Response(200, headers={"X-Frame-Options": "DENY"})
        )
        yield router


@pytest.fixture
def client(tmp_path: Path, mock: respx.MockRouter) -> Iterator[TestClient]:
    env = _env(tmp_path)
    (env.config_dir / "boards").mkdir(parents=True)
    for name, text in BOARDS.items():
        (env.config_dir / "boards" / f"{name}.yaml").write_text(text)
    (env.config_dir / "rbac.yaml").write_text(RBAC)
    with TestClient(create_app(env)) as c:
        sign_in_admin(c)
        yield c


def test_apps_list_only_workspace_embeds_with_framing(client: TestClient) -> None:
    apps = {a["widget"]: a for a in client.get("/api/v1/apps").json()["apps"]}
    assert set(apps) == {"portainer", "tautulli"}, "inline embeds are tiles, not apps"
    t = apps["tautulli"]
    assert t["board"] == "media" and t["board_title"] == "Media"
    assert t["title"] == "Tautulli" and t["url"] == "http://tautulli.lab/"
    assert t["sandbox"] == "relaxed" and t["fallback"] == "new_tab"
    assert t["state"] == "up" and t["framing"]["allowed"] is True
    p = apps["portainer"]
    assert p["title"] == "portainer"  # no title → widget id
    assert p["state"] == "degraded" and p["framing"]["allowed"] is False
    assert "X-Frame-Options" in p["framing"]["reason"] and "cannot be framed" in p["error"]


def test_app_detail_and_404s(client: TestClient) -> None:
    r = client.get("/api/v1/apps/media/tautulli")
    assert r.status_code == 200 and r.json()["url"] == "http://tautulli.lab/"
    assert client.get("/api/v1/apps/media/inline-grafana").status_code == 404
    assert client.get("/api/v1/apps/media/nope").status_code == 404
    assert client.get("/api/v1/apps/nope/tautulli").status_code == 404


def test_apps_follow_board_visibility(client: TestClient) -> None:
    r = client.post(
        "/api/v1/auth/users",
        json={"username": "house", "password": "a-long-enough-password", "groups": ["household"]},
    )
    assert r.status_code == 201, r.text
    client.post("/api/v1/auth/logout")
    r = client.post(
        "/api/v1/auth/login", json={"username": "house", "password": "a-long-enough-password"}
    )
    assert r.status_code == 200
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert [a["widget"] for a in client.get("/api/v1/apps").json()["apps"]] == ["tautulli"]
    assert client.get("/api/v1/apps/infra/portainer").status_code == 404, "hidden, not forbidden"
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/apps").status_code == 401


def test_framing_is_judged_for_the_page_the_browser_is_on(
    tmp_path: Path, mock: respx.MockRouter
) -> None:
    """Behind Traefik the browser is on https://hud.…: Host names the host and
    X-Forwarded-Proto the scheme. An app whose frame-ancestors lists that origin frames;
    the same app asked about from another origin does not, and says what to add."""
    mock.get("https://grafana.lab/").mock(
        return_value=httpx.Response(
            200, headers={"Content-Security-Policy": "frame-ancestors 'self' https://hud.test"}
        )
    )
    mock.get("http://nodered.lab:1880/").mock(return_value=httpx.Response(200))
    env = _env(tmp_path)
    (env.config_dir / "boards").mkdir(parents=True)
    (env.config_dir / "boards" / "apps.yaml").write_text(
        "apiVersion: hud/v1\nkind: Board\nmetadata: {name: apps}\nspec:\n  widgets:\n"
        "    - {id: grafana, type: embed, grid: {col: 1, row: 1}, source: {url: 'https://grafana.lab/'},"
        " display: {open_in: workspace, fallback: new_tab}}\n"
        "    - {id: nodered, type: embed, grid: {col: 2, row: 1}, source: {url: 'http://nodered.lab:1880/'},"
        " display: {open_in: workspace, fallback: new_tab}}\n"
    )
    with TestClient(create_app(env)) as client:
        sign_in_admin(client)
        behind_proxy = {"host": "hud.test", "x-forwarded-proto": "https"}
        apps = {
            a["widget"]: a for a in client.get("/api/v1/apps", headers=behind_proxy).json()["apps"]
        }
        assert apps["grafana"]["framing"]["allowed"] is True
        assert apps["nodered"]["framing"]["allowed"] is False
        assert "mixed content" in apps["nodered"]["framing"]["reason"]
        elsewhere = {"host": "other.test", "x-forwarded-proto": "https"}
        g = client.get("/api/v1/apps/apps/grafana", headers=elsewhere).json()["framing"]
        assert g["allowed"] is False and "does not include https://other.test" in g["reason"]
        # Board resolution uses the same origin (inline embeds, the layout editor's reply).
        widgets = client.get("/api/v1/boards/apps", headers=behind_proxy).json()["widgets"]
        assert {w["id"]: w["data"]["framing"]["allowed"] for w in widgets} == {
            "grafana": True,
            "nodered": False,
        }
