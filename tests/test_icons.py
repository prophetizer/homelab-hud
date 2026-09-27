# SPDX-License-Identifier: Apache-2.0
"""Service icons: fixed upstreams, validated names, cached, served locked down."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from hud.main import create_app
from hud.widgets.icons import DASHBOARD_ICONS, MAX_BYTES, MDI, IconStore, resolve
from tests.conftest import sign_in_admin
from tests.test_api_health import _env

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>'


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("sonarr.png", f"{DASHBOARD_ICONS}/png/sonarr.png"),
        ("Plex.SVG", f"{DASHBOARD_ICONS}/svg/plex.svg"),
        ("plex", f"{DASHBOARD_ICONS}/svg/plex.svg"),  # bare: the provider name
        ("home-assistant.webp", f"{DASHBOARD_ICONS}/webp/home-assistant.webp"),
        ("mdi-movie-open-star", f"{MDI}/movie-open-star.svg"),
    ],
)
def test_label_values_resolve_to_a_fixed_upstream(name: str, url: str) -> None:
    ref = resolve(name)
    assert ref is not None
    assert ref.url == url


@pytest.mark.parametrize(
    "name",
    [
        "/icons/theme-park.svg",  # Homepage's own public folder: not reachable by HUD
        "../../etc/passwd",
        "https://evil.example/x.svg",
        "sonarr.gif",
        "mdi-",
        "a b.png",
        "",
        "x" * 200 + ".png",
    ],
)
def test_anything_else_is_refused(name: str) -> None:
    assert resolve(name) is None


async def test_fetched_once_then_served_from_disk(tmp_path: Path) -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{DASHBOARD_ICONS}/png/sonarr.png").mock(
            return_value=httpx.Response(200, content=PNG)
        )
        store = IconStore(tmp_path)
        assert await store.get("sonarr.png") == (PNG, "image/png")
        assert await store.get("sonarr.png") == (PNG, "image/png")
        assert route.call_count == 1
    # A new process finds it on disk.
    assert await IconStore(tmp_path).get("sonarr.png") == (PNG, "image/png")


async def test_a_missing_icon_is_remembered(tmp_path: Path) -> None:
    with respx.mock() as mock:
        route = mock.get(f"{MDI}/nope.svg").mock(return_value=httpx.Response(404))
        store = IconStore(tmp_path)
        assert await store.get("mdi-nope") is None
        assert await IconStore(tmp_path).get("mdi-nope") is None
        assert route.call_count == 1


@pytest.mark.parametrize(
    "body",
    [b"<html><script>alert(1)</script></html>", SVG + b" " * MAX_BYTES],
    ids=["not-an-image", "too-large"],
)
async def test_only_small_real_images_are_kept(tmp_path: Path, body: bytes) -> None:
    with respx.mock() as mock:
        mock.get(f"{DASHBOARD_ICONS}/svg/plex.svg").mock(
            return_value=httpx.Response(200, content=body)
        )
        assert await IconStore(tmp_path).get("plex") is None
    assert not (tmp_path / "plex.svg").exists()


async def test_a_network_error_backs_off(tmp_path: Path) -> None:
    with respx.mock() as mock:
        route = mock.get(f"{DASHBOARD_ICONS}/svg/plex.svg").mock(
            side_effect=httpx.ConnectError("offline")
        )
        store = IconStore(tmp_path)
        assert await store.get("plex") is None
        assert await store.get("plex") is None
        assert route.call_count == 1
    # Not remembered on disk: a restart (or five minutes) tries again.
    assert not (tmp_path / "plex.svg.missing").exists()


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(_env(tmp_path))) as c:
        yield c


def test_route_needs_a_session_and_serves_svg_locked_down(client: TestClient) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{DASHBOARD_ICONS}/svg/plex.svg").mock(
            return_value=httpx.Response(200, content=SVG)
        )
        assert client.get("/api/v1/icons/plex").status_code == 401
        sign_in_admin(client)
        r = client.get("/api/v1/icons/plex")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_route_404s_a_refused_name(client: TestClient) -> None:
    sign_in_admin(client)
    assert client.get("/api/v1/icons/sonarr.gif").status_code == 404
