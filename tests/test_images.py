# SPDX-License-Identifier: Apache-2.0
"""Posters: fetched by the resource's own provider, never steered, never leaked."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from hud.api import deps
from hud.main import create_app
from hud.models import Resource, State
from hud.providers.images import MAX_IMAGE_BYTES, fetch_image, safe_image_path, sniff
from tests.conftest import sign_in_admin
from tests.test_api_health import _env
from tests.test_widgets_engine import T0

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.mark.parametrize(
    "path",
    ["/library/metadata/42/thumb/1690000000", "/Items/abc123/Images/Primary?maxHeight=240"],
)
def test_provider_relative_paths_are_accepted(path: str) -> None:
    assert safe_image_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "//evil.example/x.jpg",  # protocol-relative: names another host
        "http://evil.example/x.jpg",
        "/redirect?to=http://evil.example",
        "/a/../../etc/passwd",
        "library/metadata/1",  # not anchored at the base
        "/a b",
        "/a\\b",
        "/x#frag",
        None,
        42,
    ],
)
def test_anything_that_could_leave_the_provider_is_refused(path: Any) -> None:
    assert not safe_image_path(path)


def test_only_rasters_are_images() -> None:
    assert sniff(JPEG) == "image/jpeg"
    assert sniff(b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert sniff(b"<svg><script>alert(1)</script></svg>") is None
    assert sniff(b"<html>") is None


async def test_fetch_stays_on_the_providers_host_with_its_credentials() -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get("http://plex.test/library/metadata/1/thumb/2").mock(
            return_value=httpx.Response(200, content=JPEG)
        )
        async with httpx.AsyncClient(
            base_url="http://plex.test", params={"X-Plex-Token": "t0k"}, follow_redirects=False
        ) as client:
            assert await fetch_image(client, "/library/metadata/1/thumb/2") == (JPEG, "image/jpeg")
        assert route.calls.last.request.url.params["X-Plex-Token"] == "t0k"
        assert route.calls.last.request.headers["Accept"].startswith("image/")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"Location": "http://evil.example/x.jpg"}),
        httpx.Response(200, content=JPEG + b"\x00" * MAX_IMAGE_BYTES),
        httpx.Response(200, content=b"<html>login</html>"),
        httpx.Response(401),
    ],
    ids=["redirect-not-followed", "too-large", "not-an-image", "refused"],
)
async def test_fetch_gives_up_rather_than_serve_something_else(response: httpx.Response) -> None:
    with respx.mock() as mock:
        mock.get("http://plex.test/p").mock(return_value=response)
        async with httpx.AsyncClient(base_url="http://plex.test", follow_redirects=False) as client:
            assert await fetch_image(client, "/p") is None


class _Plex:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch_image(self, path: str) -> tuple[bytes, str] | None:
        self.calls.append(path)
        return (JPEG, "image/jpeg")


@pytest.fixture
def client(tmp_path: Path) -> Iterator[tuple[TestClient, _Plex]]:
    app = create_app(_env(tmp_path))
    with TestClient(app) as c:
        cache = app.state.cache
        cache.register_provider("plex", {})
        attrs = {"image": "/library/metadata/1/thumb/2"}
        stream = Resource(
            uid="plex:stream:7",
            provider="plex",
            kind="stream",
            name="Arrival",
            state=State.UP,
            attrs=attrs,
            fetched_at=T0,
        )
        cache.apply("plex", "sessions", [stream], [])
        plex = _Plex()
        app.state.registry.get = lambda name: plex if name == "plex" else None
        yield c, plex


def test_route_serves_a_visible_resources_image_once(client: tuple[TestClient, _Plex]) -> None:
    c, plex = client
    assert c.get("/api/v1/images/plex:stream:7").status_code == 401
    sign_in_admin(c)
    for _ in range(2):
        r = c.get("/api/v1/images/plex:stream:7")
        assert r.status_code == 200
        assert r.content == JPEG
    assert r.headers["content-type"] == "image/jpeg"
    assert "sandbox" in r.headers["content-security-policy"]
    assert plex.calls == ["/library/metadata/1/thumb/2"]  # the second came from memory
    assert c.get("/api/v1/images/plex:stream:unknown").status_code == 404


def test_route_hides_resources_the_callers_boards_do_not_show(
    client: tuple[TestClient, _Plex], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, plex = client
    sign_in_admin(c)
    monkeypatch.setattr(deps, "visible_uids", lambda request, p: set())
    assert c.get("/api/v1/images/plex:stream:7").status_code == 404
    assert plex.calls == []
