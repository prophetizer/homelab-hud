# SPDX-License-Identifier: Apache-2.0
"""Docker plugin against Engine-API-shaped fixtures through the socket-proxy URL."""

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from hud.config import ConfigManager, SecretResolver
from hud.config.schemas import ProviderDocument
from hud.models import State, Unit
from hud.providers import ProviderContext, ProviderPollError
from hud.providers.builtin.docker import DockerConfig, DockerProvider
from hud.providers.factory import ProviderFactory
from hud.providers.sdk.loader import PluginLoader
from tests.conftest import FIXTURES

PROXY = "http://socket-proxy:2375"
CONTAINERS = json.loads((FIXTURES / "docker" / "containers.json").read_text())
STATS = json.loads((FIXTURES / "docker" / "stats-hud.json").read_text())

DOC = """\
apiVersion: hud/v1
kind: Provider
metadata: { name: docker }
spec:
  plugin: docker
  defaults: { interval: 30s, jitter: 0s }
  config:
    base_url: ${DOCKER_HOST}
"""


def build(config_dir: Path, text: str = DOC, env: dict[str, str] | None = None) -> DockerProvider:
    (config_dir / "settings.yaml").write_text("apiVersion: hud/v1\nkind: Settings\n")
    (config_dir / "providers").mkdir(exist_ok=True)
    (config_dir / "providers" / "docker.yaml").write_text(text)
    snap = ConfigManager(config_dir).load()
    (doc,) = [d.model for d in snap.documents if isinstance(d.model, ProviderDocument)]
    env = env if env is not None else {"DOCKER_HOST": "tcp://socket-proxy:2375"}
    secrets = SecretResolver(config_dir, secrets_dir=config_dir / "none", env=env)
    loader = PluginLoader()
    loader.add(DockerProvider)
    p = ProviderFactory(loader)(doc, ProviderContext.create("docker", secrets, env))
    assert isinstance(p, DockerProvider)
    return p


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{PROXY}/containers/json").mock(
            return_value=httpx.Response(200, json=CONTAINERS)
        )
        router.get(f"{PROXY}/containers/hud/stats").mock(
            return_value=httpx.Response(200, json=STATS)
        )
        router.get(f"{PROXY}/containers/sonarr/stats").mock(
            return_value=httpx.Response(404, text="gone")
        )
        yield router


def test_entry_point_is_registered() -> None:
    assert "docker" in PluginLoader().available()
    assert PluginLoader().get("docker", "docker") is DockerProvider


def test_config_rewrites_tcp_and_validates() -> None:
    assert DockerConfig(base_url="tcp://socket-proxy:2375/").base_url == "http://socket-proxy:2375"
    with pytest.raises(ValueError, match="http"):
        DockerConfig(base_url="unix:///var/run/docker.sock")
    with pytest.raises(ValueError, match="invalid duration"):
        DockerConfig(timeout="soon")


async def test_discover_maps_containers(config_dir: Path, api: respx.MockRouter) -> None:
    p = build(config_dir)
    await p.startup()
    try:
        resources = await p.discover()
    finally:
        await p.shutdown()
    assert api.get(f"{PROXY}/containers/json").calls.last.request.url.params["all"] == "1"
    by_name = {r.name: r for r in resources}
    assert set(by_name) == {"hud", "sonarr", "backup", "paused-thing"}
    hud = by_name["hud"]
    assert hud.uid == "docker:container:hud" and hud.kind == "container"
    assert hud.state is State.UP
    assert hud.attrs["image"] == "ghcr.io/prophetizer/homelab-hud:edge"
    assert hud.attrs["id"] == "8dfafdbc3a40"
    assert hud.attrs["ports"] == ["8080->8080/tcp"]
    assert hud.attrs["compose_project"] == "hud" and hud.attrs["compose_service"] == "hud"
    assert hud.attrs["created"] == "2025-09-19T00:00:00+00:00"
    assert by_name["sonarr"].state is State.DEGRADED  # running but (unhealthy)
    assert by_name["sonarr"].attrs["ports"] == ["8989/tcp"]
    assert by_name["backup"].state is State.DOWN
    assert by_name["paused-thing"].state is State.PAUSED
    assert "compose_project" not in by_name["backup"].attrs


async def test_collect_stats_and_network_rates(config_dir: Path, api: respx.MockRouter) -> None:
    p = build(config_dir)
    await p.startup()
    try:
        listing = await p.poll("collect")
        first = await p.poll("stats")
        second_stats = json.loads(json.dumps(STATS))
        second_stats["networks"]["eth0"]["rx_bytes"] += 4_000_000
        second_stats["networks"]["eth0"]["tx_bytes"] += 1_000_000
        api.get(f"{PROXY}/containers/hud/stats").mock(
            return_value=httpx.Response(200, json=second_stats)
        )
        second = await p.poll("stats")
    finally:
        await p.shutdown()
    # Only running/degraded containers get a stats request; a 404 for one is not a failure.
    assert api.get(f"{PROXY}/containers/hud/stats").call_count == 2
    assert api.get(f"{PROXY}/containers/sonarr/stats").call_count == 2
    last = api.get(f"{PROXY}/containers/hud/stats").calls.last.request
    assert last.url.params["stream"] == "false"
    # State comes from collect; stats report metrics only and own no resources.
    assert len(listing.resources) == 4 and listing.metrics == []
    assert first.resources == [] and second.resources == []
    m1 = {m.name: m for m in first.metrics}
    assert all(m.resource_uid == "docker:container:hud" for m in first.metrics)
    assert set(m1) == {"cpu_pct", "mem_bytes", "mem_pct"}  # no rate without a previous sample
    # (100e6 / 4000e6) * 4 cpus * 100 = 10 %
    assert m1["cpu_pct"].value == pytest.approx(10.0) and m1["cpu_pct"].unit is Unit.PCT
    assert m1["mem_bytes"].value == 268435456 - 67108864 and m1["mem_bytes"].unit is Unit.BYTES
    assert m1["mem_pct"].value == pytest.approx((268435456 - 67108864) / 2147483648 * 100)
    m2 = {m.name: m for m in second.metrics}
    assert {"rx_bps", "tx_bps"} <= set(m2)
    assert m2["rx_bps"].unit is Unit.BPS and m2["rx_bps"].value > 0
    assert m2["rx_bps"].value == pytest.approx(m2["tx_bps"].value * 4, rel=0.05)


async def test_stats_can_be_disabled(config_dir: Path, api: respx.MockRouter) -> None:
    p = build(config_dir, DOC + "    stats: false\n")
    assert [g.name for g in p.groups()] == ["discover", "collect"]  # no stats group at all
    await p.startup()
    try:
        result = await p.poll("collect")
    finally:
        await p.shutdown()
    assert result.metrics == [] and len(result.resources) == 4
    assert not api.get(f"{PROXY}/containers/hud/stats").called


async def test_proxy_forbidden_names_the_flag(config_dir: Path, api: respx.MockRouter) -> None:
    api.get(f"{PROXY}/containers/json").mock(return_value=httpx.Response(403, text="Forbidden"))
    p = build(config_dir)
    await p.startup()
    try:
        with pytest.raises(ProviderPollError, match="CONTAINERS=1"):
            await p.discover()
    finally:
        await p.shutdown()


async def test_connection_error_is_a_poll_error(config_dir: Path, api: respx.MockRouter) -> None:
    api.get(f"{PROXY}/containers/json").mock(side_effect=httpx.ConnectError("refused"))
    p = build(config_dir)
    await p.startup()
    try:
        with pytest.raises(ProviderPollError, match="GET /containers/json: refused"):
            await p.discover()
    finally:
        await p.shutdown()


async def test_malformed_container_is_skipped(config_dir: Path, api: respx.MockRouter) -> None:
    api.get(f"{PROXY}/containers/json").mock(
        return_value=httpx.Response(
            200, json=[CONTAINERS[0], {"Names": ["/x"], "State": "running"}, "junk"]
        )
    )
    p = build(config_dir)
    await p.startup()
    try:
        resources = await p.discover()
    finally:
        await p.shutdown()
    assert [r.name for r in resources] == ["hud", "x"]


async def test_stats_cap_picks_a_stable_set_by_name(
    config_dir: Path, api: respx.MockRouter
) -> None:
    """Docker lists newest-created first. Under the cap, a container recreated by an image
    update must not push another out of the stats set — that would silently gap the
    history of whichever container fell off. Selection is by name, so it holds still."""
    p = build(config_dir, DOC + "    max_stats: 1\n")
    await p.startup()
    try:
        await p.poll("collect")  # listing order: hud, sonarr
        await p.poll("stats")
        assert api.get(f"{PROXY}/containers/hud/stats").call_count == 1
        # sonarr is recreated and now lists first; the stats set must not follow it.
        api.get(f"{PROXY}/containers/json").mock(
            return_value=httpx.Response(200, json=list(reversed(CONTAINERS)))
        )
        await p.poll("collect")
        await p.poll("stats")
    finally:
        await p.shutdown()
    assert api.get(f"{PROXY}/containers/hud/stats").call_count == 2
    assert not api.get(f"{PROXY}/containers/sonarr/stats").called


def test_state_and_stats_are_separate_groups_with_separate_budgets(config_dir: Path) -> None:
    """Found on a live 103-container stack: one shared poll meant a slow stats pass timed
    out container state too. State now has a listing-sized budget; stats get their own."""
    p = build(config_dir, DOC + "    max_stats: 120\n")
    groups = {g.name: g for g in p.groups()}
    assert set(groups) == {"discover", "collect", "stats"}
    assert groups["collect"].timeout == groups["discover"].timeout == 15.0  # 10s + 5s
    assert groups["stats"].timeout > 60  # 30 batches of ~2s, plus the request budget
    assert groups["stats"].interval == groups["collect"].interval


async def test_a_failed_stats_pass_does_not_stale_container_state(
    config_dir: Path, api: respx.MockRouter
) -> None:
    """The property the split exists for. The cache marks stale only what a failing group
    owns, and the stats group owns nothing — so a timed-out stats pass leaves every
    container's up/down state fresh."""
    from hud.collector import LiveCache  # noqa: PLC0415

    p = build(config_dir)
    await p.startup()
    try:
        state = await p.poll("collect")
        stats = await p.poll("stats")
    finally:
        await p.shutdown()
    cache = LiveCache()
    cache.apply("docker", "collect", state.resources, state.metrics)
    cache.apply("docker", "stats", stats.resources, stats.metrics)
    assert cache.metric("docker:container:hud", "cpu_pct") is not None
    cache.mark_stale("docker", "stats", "timed out after 75s")
    assert all(not r.stale for r in cache.resources()), "stats failure staled container state"
    # Whereas a failed listing does, as it should.
    cache.mark_stale("docker", "collect", "GET /containers/json: timed out")
    assert all(r.stale for r in cache.resources())


async def test_listing_does_not_share_the_stats_connection_pool(
    config_dir: Path, api: respx.MockRouter
) -> None:
    """The discover listing was starved of connections by a stats pass holding all four.
    The two now use separate clients: listing still works with the stats client gone."""
    p = build(config_dir)
    await p.startup()
    try:
        assert p._client is not None and p._stats_client is not None
        assert p._client is not p._stats_client
        await p._stats_client.aclose()  # simulate a stats pool that is fully tied up
        p._stats_client = None
        assert len((await p.poll("collect")).resources) == 4
        assert len(await p.discover()) == 4
    finally:
        await p.shutdown()


async def test_one_shot_stats_compute_cpu_from_the_previous_sample(
    config_dir: Path, api: respx.MockRouter
) -> None:
    """Found live: two-sample stats (~1s each) over 103 containers overran the pass. With
    one-shot the engine answers at once but leaves precpu_stats zeroed, so CPU % comes from
    this container's previous sample — absent on the first pass, like network rates."""
    one_shot = json.loads(json.dumps(STATS))
    # As Docker 29 / API 1.56 actually sends it: system_cpu_usage absent, not zero.
    one_shot["precpu_stats"] = {"cpu_usage": {"total_usage": 0}}
    api.get(f"{PROXY}/containers/hud/stats").mock(return_value=httpx.Response(200, json=one_shot))
    p = build(config_dir)
    await p.startup()
    try:
        await p.poll("collect")
        first = await p.poll("stats")
        later = json.loads(json.dumps(one_shot))
        later["cpu_stats"]["cpu_usage"]["total_usage"] += 100_000_000
        later["cpu_stats"]["system_cpu_usage"] += 4_000_000_000
        api.get(f"{PROXY}/containers/hud/stats").mock(return_value=httpx.Response(200, json=later))
        second = await p.poll("stats")
    finally:
        await p.shutdown()
    request = api.get(f"{PROXY}/containers/hud/stats").calls.last.request
    assert request.url.params["one-shot"] == "true" and request.url.params["stream"] == "false"
    assert "cpu_pct" not in {m.name for m in first.metrics}  # nothing to diff against yet
    assert {"mem_bytes", "mem_pct"} <= {m.name for m in first.metrics}  # memory needs no history
    cpu = {m.name: m for m in second.metrics}["cpu_pct"]
    # (100e6 / 4000e6) * 4 online cpus * 100 = 10 %
    assert cpu.value == pytest.approx(10.0) and cpu.unit is Unit.PCT
