# SPDX-License-Identifier: Apache-2.0
"""Sonarr plugin against v3-API-shaped fixtures."""

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from hud.config import ConfigError, ConfigManager, SecretResolver
from hud.config.schemas import ProviderDocument
from hud.models import Severity, State, Unit
from hud.providers import ProviderBuildError, ProviderContext, ProviderPollError
from hud.providers.builtin.sonarr import SonarrProvider
from hud.providers.factory import ProviderFactory
from hud.providers.sdk.loader import PluginLoader
from tests.conftest import FIXTURES

URL = "http://sonarr.lab:8989"
F = FIXTURES / "sonarr"
STATUS = json.loads((F / "system-status.json").read_text())
HEALTH = json.loads((F / "health.json").read_text())
QUEUE = json.loads((F / "queue.json").read_text())

DOC = """\
apiVersion: hud/v1
kind: Provider
metadata: { name: sonarr, labels: { category: media } }
spec:
  plugin: sonarr
  defaults: { interval: 30s }
  config:
    base_url: http://sonarr.lab:8989/
    api_key: ${secret:sonarr_api_key}
"""


def build(config_dir: Path, text: str = DOC, env: dict[str, str] | None = None) -> SonarrProvider:
    (config_dir / "settings.yaml").write_text("apiVersion: hud/v1\nkind: Settings\n")
    (config_dir / "providers").mkdir(exist_ok=True)
    (config_dir / "providers" / "sonarr.yaml").write_text(text)
    snap = ConfigManager(config_dir).load()
    if snap.quarantined:  # the file was refused; surface why, as a load error would
        raise ConfigError(snap.quarantined[0].issues)
    (doc,) = [d.model for d in snap.documents if isinstance(d.model, ProviderDocument)]
    env = env if env is not None else {"HUD_SECRET_SONARR_API_KEY": "k" * 32}
    secrets = SecretResolver(config_dir, secrets_dir=config_dir / "none", env=env)
    loader = PluginLoader()
    loader.add(SonarrProvider)
    p = ProviderFactory(loader)(doc, ProviderContext.create("sonarr", secrets, env))
    assert isinstance(p, SonarrProvider)
    return p


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{URL}/api/v3/system/status").mock(
            return_value=httpx.Response(200, json=STATUS)
        )
        router.get(f"{URL}/api/v3/health").mock(return_value=httpx.Response(200, json=HEALTH))
        router.get(f"{URL}/api/v3/queue").mock(return_value=httpx.Response(200, json=QUEUE))
        yield router


def test_entry_point_and_literal_key_refused(config_dir: Path) -> None:
    assert PluginLoader().get("sonarr", "sonarr") is SonarrProvider
    # The config scanner catches a literal api_key before the plugin ever sees it.
    with pytest.raises(ConfigError, match="looks like a literal secret"):
        build(config_dir, DOC.replace("${secret:sonarr_api_key}", "a" * 32))
    with pytest.raises(ProviderBuildError, match="secret 'sonarr_api_key' not found"):
        build(config_dir, env={})


async def test_discover_service_with_version_and_health(
    config_dir: Path, api: respx.MockRouter
) -> None:
    p = build(config_dir)
    await p.startup()
    try:
        (service,) = await p.discover()
    finally:
        await p.shutdown()
    sent = api.get(f"{URL}/api/v3/system/status").calls.last.request
    assert sent.headers["X-Api-Key"] == "k" * 32
    assert service.uid == "sonarr:service:main" and service.name == "Sonarr"
    assert service.state is State.UP  # a warning does not degrade; an error does
    assert service.attrs["version"] == "4.0.9.2244"
    assert service.attrs["branch"] == "main" and service.attrs["os"] == "ubuntu 22.04"
    assert service.attrs["health_warnings"] == 1 and service.attrs["health_errors"] == 0
    assert service.attrs["health_messages"] == ["Indexers unavailable due to failures: NZBgeek"]
    assert service.links == {"ui": URL}
    assert p.labels == {"category": "media"}


async def test_collect_queue_metrics_and_health_events(
    config_dir: Path, api: respx.MockRouter
) -> None:
    p = build(config_dir)
    await p.startup()
    try:
        result = await p.poll("collect")  # runs discover first
        again = await p.poll("collect")
        resolved_health = [h for h in HEALTH if h["type"] != "warning"] + [
            {"source": "DownloadClientCheck", "type": "error", "message": "SABnzbd unreachable"}
        ]
        api.get(f"{URL}/api/v3/health").mock(return_value=httpx.Response(200, json=resolved_health))
        third = await p.poll("collect")
    finally:
        await p.shutdown()
    sent = api.get(f"{URL}/api/v3/queue").calls.last.request
    assert sent.url.params["pageSize"] == "100"

    by_uid = {r.uid: r for r in result.resources}
    assert set(by_uid) == {
        "sonarr:service:main",
        "sonarr:download:1786421",
        "sonarr:download:1786422",
        "sonarr:download:1786423",
    }
    d1 = by_uid["sonarr:download:1786421"]
    assert d1.state is State.UP and d1.parent_uid == "sonarr:service:main"
    assert d1.name == "The.Expanse.S06E03.1080p.WEB.H264-GLHF"
    assert d1.attrs["series"] == "The Expanse"
    assert (d1.attrs["season"], d1.attrs["episode"]) == (6, 3)
    assert d1.attrs["download_client"] == "SABnzbd" and d1.attrs["time_left"] == "00:04:12"
    assert by_uid["sonarr:download:1786422"].state is State.PAUSED
    assert by_uid["sonarr:download:1786423"].state is State.DEGRADED  # tracked warning

    metrics = {(m.resource_uid, m.name): m for m in result.metrics}
    assert metrics[("sonarr:service:main", "queue_size")].value == 3
    assert metrics[("sonarr:service:main", "queue_size")].unit is Unit.COUNT
    assert metrics[("sonarr:service:main", "queue_bytes_left")].value == 536870912 + 2147483648
    assert metrics[("sonarr:download:1786421", "progress_pct")].value == pytest.approx(75.0)
    assert metrics[("sonarr:download:1786422", "progress_pct")].value == 0.0
    assert ("sonarr:download:1786423", "progress_pct") not in metrics  # size 0: no progress

    # Health items become events once, not on every poll; clearing emits 'resolved'.
    assert [(e.type, e.severity, e.message) for e in result.events] == [
        (
            "health",
            Severity.WARN,
            "IndexerStatusCheck: Indexers unavailable due to failures: NZBgeek",
        ),
        ("health", Severity.INFO, "UpdateCheck: New update is available: 4.0.10.2544"),
    ]
    assert again.events == []
    assert [(e.type, e.severity) for e in third.events] == [
        ("health", Severity.ERROR),
        ("resolved", Severity.INFO),
    ]
    assert third.resources[0].state is State.DEGRADED  # an error-level health item


async def test_api_key_rejected_and_bad_queue(config_dir: Path, api: respx.MockRouter) -> None:
    p = build(config_dir)
    await p.startup()
    try:
        api.get(f"{URL}/api/v3/system/status").mock(return_value=httpx.Response(401, text="no"))
        with pytest.raises(ProviderPollError, match="401 — API key rejected"):
            await p.discover()
        api.get(f"{URL}/api/v3/system/status").mock(return_value=httpx.Response(200, json=STATUS))
        api.get(f"{URL}/api/v3/queue").mock(return_value=httpx.Response(200, json={"oops": 1}))
        with pytest.raises(ProviderPollError, match="no records list"):
            await p.poll("collect")
        api.get(f"{URL}/api/v3/queue").mock(
            return_value=httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
        )
        with pytest.raises(ProviderPollError, match="not JSON"):
            await p.poll("collect")
    finally:
        await p.shutdown()


async def test_queue_items_carry_cover_paths_and_the_stuck_reason(
    config_dir: Path, api: respx.MockRouter
) -> None:
    """Posters come from series.images (includeSeries=true) as paths on Sonarr, fetched
    later through this provider's own client; the browser never sees the key."""
    queue = json.loads(json.dumps(QUEUE))
    rec = queue["records"][0]
    rec["series"]["images"] = [
        {"coverType": "poster", "url": "/MediaCover/7/poster.jpg?lastWrite=1"},
        {"coverType": "fanart", "url": "/MediaCover/7/fanart.jpg?lastWrite=1"},
    ]
    rec["statusMessages"] = [{"title": "x", "messages": ["No files found are eligible for import"]}]
    route = api.get(f"{URL}/api/v3/queue").mock(return_value=httpx.Response(200, json=queue))
    p = build(config_dir)
    await p.startup()
    try:
        result = await p.poll("collect")
        assert route.calls.last.request.url.params["includeSeries"] == "true"
        download = next(r for r in result.resources if r.kind == "download")
        assert download.attrs["image"] == "/MediaCover/7/poster.jpg?lastWrite=1"
        assert download.attrs["backdrop"] == "/MediaCover/7/fanart.jpg?lastWrite=1"
        assert download.attrs["reason"] == "No files found are eligible for import"
        health = {m.name: m.value for m in result.metrics if m.name.startswith("health_")}
        assert set(health) == {"health_errors", "health_warnings"}
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 16
        img = api.get(f"{URL}/MediaCover/7/poster.jpg").mock(
            return_value=httpx.Response(200, content=jpeg)
        )
        assert await p.fetch_image("/MediaCover/7/poster.jpg?lastWrite=1") == (jpeg, "image/jpeg")
        assert img.calls.last.request.headers["X-Api-Key"] == "k" * 32  # its own auth, server-side
        assert await p.fetch_image("//evil.example/x.jpg") is None  # never another host
    finally:
        await p.shutdown()
