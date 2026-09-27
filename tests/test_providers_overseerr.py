# SPDX-License-Identifier: Apache-2.0
"""Overseerr / Jellyseerr: requests titled and postered, each title looked up once, and
posters fetched through the service's own image proxy with its key."""

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from hud.config import ConfigError, ConfigManager, SecretResolver
from hud.config.schemas import ProviderDocument
from hud.models import State
from hud.providers import ProviderContext
from hud.providers.builtin.overseerr import Overseerr, request_state
from hud.providers.factory import ProviderFactory
from hud.providers.sdk.loader import PluginLoader

URL = "http://requests.lab:5055"
KEY = "k" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64

DOC = """\
apiVersion: hud/v1
kind: Provider
metadata: { name: jellyseerr }
spec:
  plugin: overseerr
  defaults: { interval: 60s }
  config:
    base_url: http://requests.lab:5055
    api_key: ${secret:jellyseerr_api_key}
    title: Jellyseerr
"""

REQUESTS: dict[str, Any] = {
    "pageInfo": {"pages": 1, "results": 4},
    "results": [
        {
            "id": 91,
            "status": 1,
            "type": "movie",
            "is4k": False,
            "createdAt": "2026-09-27T10:00:00Z",
            "media": {"tmdbId": 101, "mediaType": "movie", "status": 2},
            "requestedBy": {"displayName": "Guest One"},
        },
        {
            "id": 90,
            "status": 2,
            "type": "tv",
            "is4k": False,
            "createdAt": "2026-09-26T10:00:00Z",
            "media": {"tmdbId": 202, "mediaType": "tv", "status": 5},
            "requestedBy": {"displayName": "Guest Two"},
        },
        {
            "id": 89,
            "status": 4,
            "type": "movie",
            "is4k": True,
            "createdAt": "2026-09-25T10:00:00Z",
            "media": {"tmdbId": 101, "mediaType": "movie", "status": 3},
        },
        {
            "id": 88,
            "status": 1,
            "type": "movie",
            "is4k": False,
            "createdAt": "2026-09-24T10:00:00Z",
            "media": {"tmdbId": 101, "mediaType": "movie", "status": 2},
        },  # older duplicate
    ],
}
COUNT = {
    "total": 40,
    "movie": 25,
    "tv": 15,
    "pending": 2,
    "approved": 30,
    "declined": 1,
    "processing": 3,
    "available": 34,
}
MOVIE = {
    "title": "Paper Moons",
    "releaseDate": "2026-10-03",
    "posterPath": "/pm.jpg",
    "backdropPath": "/pmb.jpg",
}
SHOW = {"name": "The Long Orbit", "firstAirDate": "2024-02-01", "posterPath": "/lo.jpg"}


def build(config_dir: Path, text: str = DOC) -> Overseerr:
    (config_dir / "settings.yaml").write_text("apiVersion: hud/v1\nkind: Settings\n")
    (config_dir / "providers").mkdir(exist_ok=True)
    (config_dir / "providers" / "jellyseerr.yaml").write_text(text)
    snap = ConfigManager(config_dir).load()
    if snap.quarantined:
        raise ConfigError(snap.quarantined[0].issues)
    (doc,) = [d.model for d in snap.documents if isinstance(d.model, ProviderDocument)]
    env = {"HUD_SECRET_JELLYSEERR_API_KEY": KEY}
    secrets = SecretResolver(config_dir, secrets_dir=config_dir / "none", env=env)
    loader = PluginLoader()
    loader.add(Overseerr)
    p = ProviderFactory(loader)(doc, ProviderContext.create("jellyseerr", secrets, env))
    assert isinstance(p, Overseerr)
    return p


@pytest.fixture
def api() -> respx.MockRouter:
    router = respx.mock(assert_all_called=False)
    router.get(f"{URL}/api/v1/status").mock(
        return_value=httpx.Response(200, json={"version": "2.5.2", "updateAvailable": False})
    )
    router.get(f"{URL}/api/v1/request/count").mock(return_value=httpx.Response(200, json=COUNT))
    router.get(f"{URL}/api/v1/request").mock(return_value=httpx.Response(200, json=REQUESTS))
    router.get(f"{URL}/api/v1/movie/101").mock(return_value=httpx.Response(200, json=MOVIE))
    router.get(f"{URL}/api/v1/tv/202").mock(return_value=httpx.Response(200, json=SHOW))
    return router


async def test_requests_are_titled_postered_and_keyed_by_media(
    config_dir: Path, api: respx.MockRouter
) -> None:
    p = build(config_dir)
    with api:
        await p.startup()
        try:
            first = await p.poll("collect")
            await p.poll("collect")
        finally:
            await p.shutdown()
        # One lookup per title, however many polls and requests name it.
        assert api.routes[3].call_count == 1 and api.routes[4].call_count == 1
        assert api.routes[2].calls.last.request.headers["X-Api-Key"] == KEY
    by_uid = {r.uid: r for r in first.resources}
    assert set(by_uid) == {
        "jellyseerr:service:main",
        "jellyseerr:request:movie-101",
        "jellyseerr:request:tv-202",
        "jellyseerr:request:movie-101-4k",
    }
    movie = by_uid["jellyseerr:request:movie-101"]
    assert movie.name == "Paper Moons" and movie.state is State.DEGRADED  # awaiting approval
    assert movie.attrs["requested_by"] == "Guest One"  # the newest request, not the older one
    assert movie.attrs["image"] == "/w300/pm.jpg"
    assert movie.attrs["backdrop"] == "/w780/pmb.jpg"
    assert movie.attrs["year"] == "2026"
    show = by_uid["jellyseerr:request:tv-202"]
    assert show.name == "The Long Orbit" and show.state is State.UP  # available
    assert show.attrs["backdrop"] is None
    assert by_uid["jellyseerr:request:movie-101-4k"].state is State.DOWN  # failed
    counts = {m.name: m.value for m in first.metrics}
    assert counts["requests_pending"] == 2 and counts["requests_available"] == 34
    assert by_uid["jellyseerr:service:main"].attrs["version"] == "2.5.2"


async def test_posters_come_through_the_services_image_proxy(
    config_dir: Path, api: respx.MockRouter
) -> None:
    p = build(config_dir)
    with api:
        proxy = api.get(f"{URL}/imageproxy/tmdb/t/p/w300/pm.jpg").mock(
            return_value=httpx.Response(200, content=JPEG)
        )
        await p.startup()
        try:
            assert await p.fetch_image("/w300/pm.jpg") == (JPEG, "image/jpeg")
            assert await p.fetch_image("//evil.example/x.jpg") is None
        finally:
            await p.shutdown()
        assert proxy.calls.last.request.headers["X-Api-Key"] == KEY


async def test_tmdb_posters_never_carry_the_key(config_dir: Path, api: respx.MockRouter) -> None:
    p = build(config_dir, DOC.replace("title: Jellyseerr", "title: Jellyseerr\n    posters: tmdb"))
    with api:
        tmdb = api.get("https://image.tmdb.org/t/p/w300/pm.jpg").mock(
            return_value=httpx.Response(200, content=JPEG)
        )
        await p.startup()
        try:
            assert await p.fetch_image("/w300/pm.jpg") == (JPEG, "image/jpeg")
        finally:
            await p.shutdown()
        assert "X-Api-Key" not in tmdb.calls.last.request.headers


async def test_a_failed_title_lookup_is_an_untitled_row(
    config_dir: Path, api: respx.MockRouter
) -> None:
    p = build(config_dir)
    with api:
        api.get(f"{URL}/api/v1/movie/101").mock(return_value=httpx.Response(500))
        await p.startup()
        try:
            result = await p.poll("collect")
        finally:
            await p.shutdown()
    names = {r.uid: r.name for r in result.resources}
    assert names["jellyseerr:request:movie-101"] == "TMDB 101"
    assert names["jellyseerr:request:tv-202"] == "The Long Orbit"


@pytest.mark.parametrize(
    ("request_status", "media_status", "state"),
    [
        (1, 2, State.DEGRADED),
        (2, 3, State.UNKNOWN),
        (3, 1, State.UNKNOWN),
        (4, 2, State.DOWN),
        (2, 4, State.UP),
        (1, 5, State.UP),
        (5, 2, State.UP),
    ],
)
def test_request_state(request_status: int, media_status: int, state: State) -> None:
    assert request_state(request_status, media_status) is state
