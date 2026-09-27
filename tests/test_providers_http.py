# SPDX-License-Identifier: Apache-2.0
"""HTTP checks: an app answering is up, a broken route is down, slow is degraded."""

from pathlib import Path

import httpx
import pytest
import respx

from hud.config import ConfigError, ConfigManager, SecretResolver
from hud.config.schemas import ProviderDocument
from hud.models import State, Unit
from hud.providers import ProviderBuildError, ProviderContext
from hud.providers.builtin.http import HttpChecks, classify
from hud.providers.factory import ProviderFactory
from hud.providers.sdk.loader import PluginLoader

DOC = """\
apiVersion: hud/v1
kind: Provider
metadata: { name: web }
spec:
  plugin: http
  defaults: { interval: 60s }
  config:
    timeout: 2s
    checks:
      - { name: sonarr, url: "https://sonarr.lab.example", title: Sonarr }
      - { name: sso, url: "https://auth.lab.example" }
      - { name: gated, url: "https://gated.lab.example" }
      - { name: missing, url: "https://missing.lab.example" }
      - { name: broken, url: "https://broken.lab.example" }
      - { name: dead, url: "https://dead.lab.example" }
      - { name: strict, url: "https://strict.lab.example/health", expect: [200] }
"""


def build(config_dir: Path, text: str = DOC) -> HttpChecks:
    (config_dir / "settings.yaml").write_text("apiVersion: hud/v1\nkind: Settings\n")
    (config_dir / "providers").mkdir(exist_ok=True)
    (config_dir / "providers" / "web.yaml").write_text(text)
    snap = ConfigManager(config_dir).load()
    if snap.quarantined:
        raise ConfigError(snap.quarantined[0].issues)
    (doc,) = [d.model for d in snap.documents if isinstance(d.model, ProviderDocument)]
    secrets = SecretResolver(config_dir, secrets_dir=config_dir / "none", env={})
    loader = PluginLoader()
    loader.add(HttpChecks)
    p = ProviderFactory(loader)(doc, ProviderContext.create("web", secrets, {}))
    assert isinstance(p, HttpChecks)
    return p


@pytest.mark.parametrize(
    ("status", "seconds", "expect", "state"),
    [
        (200, 0.1, None, State.UP),
        (302, 0.1, None, State.UP),  # a redirect to sign-in: the app answered
        (401, 0.1, None, State.UP),  # an auth gate answered
        (403, 0.1, None, State.UP),
        (404, 0.1, None, State.DOWN),  # a proxy's 404 for a missing route is an outage
        (502, 0.1, None, State.DOWN),
        (None, 2.0, None, State.DOWN),  # timeout or refused
        (200, 3.0, None, State.DEGRADED),  # answered, slowly
        (302, 0.1, [200], State.DOWN),  # a strict health check wants exactly 200
    ],
)
def test_classify(
    status: int | None, seconds: float, expect: list[int] | None, state: State
) -> None:
    assert classify(status, seconds, 2.0, expect) is state


async def test_collect_checks_every_endpoint_without_following_redirects(config_dir: Path) -> None:
    p = build(config_dir)
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://sonarr.lab.example").mock(return_value=httpx.Response(200))
        mock.get("https://auth.lab.example").mock(
            return_value=httpx.Response(302, headers={"Location": "https://sso.lab.example/login"})
        )
        mock.get("https://gated.lab.example").mock(return_value=httpx.Response(401))
        mock.get("https://missing.lab.example").mock(return_value=httpx.Response(404))
        mock.get("https://broken.lab.example").mock(return_value=httpx.Response(502))
        mock.get("https://dead.lab.example").mock(side_effect=httpx.ConnectError("refused"))
        mock.get("https://strict.lab.example/health").mock(return_value=httpx.Response(200))
        await p.startup()
        try:
            result = await p.poll("collect")
        finally:
            await p.shutdown()
    states = {r.uid: r.state for r in result.resources}
    assert states == {
        "web:endpoint:sonarr": State.UP,
        "web:endpoint:sso": State.UP,
        "web:endpoint:gated": State.UP,
        "web:endpoint:missing": State.DOWN,
        "web:endpoint:broken": State.DOWN,
        "web:endpoint:dead": State.DOWN,
        "web:endpoint:strict": State.UP,
    }
    by_uid = {r.uid: r for r in result.resources}
    assert by_uid["web:endpoint:sonarr"].name == "Sonarr"
    assert by_uid["web:endpoint:dead"].attrs["reason"] == "ConnectError"
    assert by_uid["web:endpoint:broken"].links == {"ui": "https://broken.lab.example"}
    timing = [m for m in result.metrics if m.name == "response_seconds"]
    assert len(timing) == 7 and all(m.unit is Unit.SECONDS for m in timing)
    codes = {m.resource_uid: m.value for m in result.metrics if m.name == "status_code"}
    assert codes["web:endpoint:missing"] == 404 and "web:endpoint:dead" not in codes


def test_config_refuses_duplicate_names_and_relative_urls(config_dir: Path) -> None:
    """Plugin config is checked when the provider builds: a failed build, shown as that
    provider failing with the reason (invariant 6), not a quarantined file."""
    dupes = DOC.replace("name: sso", "name: sonarr")
    with pytest.raises(ProviderBuildError, match="duplicate check names"):
        build(config_dir, dupes)
    relative = DOC.replace('"https://auth.lab.example"', '"/auth"')
    with pytest.raises(ProviderBuildError, match="absolute"):
        build(config_dir, relative)
