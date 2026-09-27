# SPDX-License-Identifier: Apache-2.0
"""qBittorrent: signs in once and again only when the session lapses, maps torrents by
infohash with progress as a percentage, and says plainly when sign-in is refused."""

from pathlib import Path

import httpx
import pytest
import respx

from hud.config import ConfigError, ConfigManager, SecretResolver
from hud.config.schemas import ProviderDocument
from hud.models import State, Unit
from hud.providers import ProviderBuildError, ProviderContext, ProviderPollError
from hud.providers.builtin.qbittorrent import Qbittorrent, torrent_state
from hud.providers.factory import ProviderFactory
from hud.providers.sdk.loader import PluginLoader

URL = "http://torrents.lab:8080"

DOC = """\
apiVersion: hud/v1
kind: Provider
metadata: { name: qbittorrent }
spec:
  plugin: qbittorrent
  defaults: { interval: 15s }
  config:
    base_url: http://torrents.lab:8080
    username: hud
    password: ${secret:qbittorrent_password}
"""

TORRENTS = [
    {
        "hash": "ABC123",
        "name": "linux-distro.iso",
        "state": "downloading",
        "progress": 0.42,
        "size": 4_000_000_000,
        "dlspeed": 2_500_000,
        "eta": 900,
        "category": "iso",
        "added_on": 1_790_000_000,
        "ratio": 0.1,
    },
    {
        "hash": "def456",
        "name": "stalled.mkv",
        "state": "stalledDL",
        "progress": 0.1,
        "size": 1_000,
        "dlspeed": 0,
        "eta": 8_640_000,
    },
    {"hash": "", "name": "no hash"},
]


def build(config_dir: Path, text: str = DOC) -> Qbittorrent:
    (config_dir / "settings.yaml").write_text("apiVersion: hud/v1\nkind: Settings\n")
    (config_dir / "providers").mkdir(exist_ok=True)
    (config_dir / "providers" / "qbittorrent.yaml").write_text(text)
    snap = ConfigManager(config_dir).load()
    if snap.quarantined:
        raise ConfigError(snap.quarantined[0].issues)
    (doc,) = [d.model for d in snap.documents if isinstance(d.model, ProviderDocument)]
    env = {"HUD_SECRET_QBITTORRENT_PASSWORD": "fixture-password-000"}
    secrets = SecretResolver(config_dir, secrets_dir=config_dir / "none", env=env)
    loader = PluginLoader()
    loader.add(Qbittorrent)
    p = ProviderFactory(loader)(doc, ProviderContext.create("qbittorrent", secrets, env))
    assert isinstance(p, Qbittorrent)
    return p


def _api(router: respx.MockRouter) -> respx.Route:
    login = router.post(f"{URL}/api/v2/auth/login").mock(
        return_value=httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=s1; path=/"})
    )
    router.get(f"{URL}/api/v2/app/version").mock(return_value=httpx.Response(200, text="v5.0.1"))
    router.get(f"{URL}/api/v2/transfer/info").mock(
        return_value=httpx.Response(
            200,
            json={
                "dl_info_speed": 3_000_000,
                "up_info_speed": 50_000,
                "connection_status": "firewalled",
            },
        )
    )
    router.get(f"{URL}/api/v2/torrents/info").mock(return_value=httpx.Response(200, json=TORRENTS))
    return login


async def test_torrents_and_speeds(config_dir: Path) -> None:
    p = build(config_dir)
    with respx.mock() as router:
        login = _api(router)
        await p.startup()
        try:
            result = await p.poll("collect")
            await p.poll("collect")
        finally:
            await p.shutdown()
        assert login.call_count == 1  # the session cookie is reused
        form = login.calls.last.request.content.decode()
        assert "username=hud" in form
        assert login.calls.last.request.headers["Referer"] == URL
        sent = router.routes[3].calls.last.request.url.params
        assert sent["filter"] == "downloading" and sent["sort"] == "added_on"
    by_uid = {r.uid: r for r in result.resources}
    assert set(by_uid) == {
        "qbittorrent:service:main",
        "qbittorrent:download:abc123",
        "qbittorrent:download:def456",
    }
    assert by_uid["qbittorrent:service:main"].state is State.DEGRADED  # firewalled
    assert by_uid["qbittorrent:service:main"].attrs["version"] == "v5.0.1"
    assert by_uid["qbittorrent:download:def456"].state is State.DEGRADED  # stalled
    assert by_uid["qbittorrent:download:def456"].attrs["eta_seconds"] is None  # infinite
    metrics = {(m.resource_uid, m.name): (m.value, m.unit) for m in result.metrics}
    assert metrics[("qbittorrent:download:abc123", "progress_pct")] == (
        pytest.approx(42.0),
        Unit.PCT,
    )
    assert metrics[("qbittorrent:service:main", "speed_bps")][1] is Unit.BPS
    assert metrics[("qbittorrent:service:main", "torrents")][0] == 2


async def test_an_expired_session_signs_in_once_more(config_dir: Path) -> None:
    p = build(config_dir)
    with respx.mock() as router:
        login = _api(router)
        info = router.routes[2]
        await p.startup()
        try:
            await p.poll("collect")
            info.side_effect = [
                httpx.Response(403),
                httpx.Response(200, json={"connection_status": "connected"}),
            ]
            result = await p.poll("collect")
        finally:
            await p.shutdown()
        assert login.call_count == 2
    assert result.resources[0].state is State.UP


async def test_rejected_credentials_fail_the_provider_with_the_reason(config_dir: Path) -> None:
    p = build(config_dir)
    with respx.mock(assert_all_called=False) as router:
        _api(router)
        router.post(f"{URL}/api/v2/auth/login").mock(
            return_value=httpx.Response(200, text="Fails.")
        )
        await p.startup()
        try:
            with pytest.raises(ProviderPollError, match="credentials rejected"):
                await p.poll("collect")
        finally:
            await p.shutdown()


def test_username_without_password_is_refused(config_dir: Path) -> None:
    with pytest.raises(ProviderBuildError, match="both username and password"):
        build(config_dir, DOC.replace("    password: ${secret:qbittorrent_password}\n", ""))


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("uploading", State.UP),
        ("pausedDL", State.PAUSED),
        ("stoppedUP", State.PAUSED),
        ("missingFiles", State.DOWN),
        ("stalledDL", State.DEGRADED),
        ("newState", State.UNKNOWN),
        (None, State.UNKNOWN),
    ],
)
def test_torrent_state(state: object, expected: State) -> None:
    assert torrent_state(state) is expected
