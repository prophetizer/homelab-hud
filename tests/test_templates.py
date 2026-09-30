# SPDX-License-Identifier: Apache-2.0
"""Every bundled template validates, carries catalog metadata, and still maps its fixture
to the expected resources and metrics (PLAN.md §8.5.1: an upstream API change surfaces
here, not in someone's issue tracker).

Layout: ``templates/providers/<name>.yaml`` with
``templates/providers/fixtures/<name>/<resource>.json`` (one response per resources[]
entry) and ``expected.json`` (uid → state, and "uid/metric" → [value, unit]).
"""

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from hud.config import ConfigManager, SecretResolver
from hud.config.interpolate import env_refs
from hud.config.schemas import ProviderDocument
from hud.providers import ProviderContext
from hud.providers.declarative import build_declarative

TEMPLATES = Path(__file__).parent.parent / "templates" / "providers"
TEMPLATE_FILES = sorted(TEMPLATES.glob("*.yaml"))
BASE = "http://template.test"


def _load(config_dir: Path, template: Path) -> ProviderDocument:
    (config_dir / "settings.yaml").write_text("apiVersion: hud/v1\nkind: Settings\n")
    (config_dir / "providers").mkdir(exist_ok=True)
    (config_dir / "providers" / template.name).write_text(template.read_text())
    snap = ConfigManager(config_dir).load()
    assert snap.warnings == (), [str(w) for w in snap.warnings]
    (doc,) = [d.model for d in snap.documents if isinstance(d.model, ProviderDocument)]
    return doc


def test_at_least_one_template_is_bundled() -> None:
    assert TEMPLATE_FILES, "templates/providers is empty"


@pytest.mark.parametrize("template", TEMPLATE_FILES, ids=lambda p: p.stem)
def test_template_validates_with_catalog_metadata(config_dir: Path, template: Path) -> None:
    doc = _load(config_dir, template)
    assert doc.metadata.name == template.stem, "file name must equal metadata.name"
    info = doc.metadata.template
    assert info is not None, "bundled templates carry metadata.template (§8.5.1)"
    assert info.service and info.docs
    # Every placeholder the template uses is documented in `requires`, and nothing else.
    text = template.read_text()
    used = {("env", name) for name in env_refs(text)}
    used |= {("secret", m) for m in re.findall(r"\$\{secret:([A-Za-z0-9_.-]+)\}", text)}
    declared = {(r.kind, r.name) for r in info.requires}
    assert declared == used, f"requires must list exactly {sorted(used)}"
    fixtures = TEMPLATES / "fixtures" / template.stem
    for res in doc.spec.resources:
        assert (fixtures / f"{res.name}.json").is_file(), f"missing fixture {res.name}.json"
    assert (fixtures / "expected.json").is_file()


@pytest.mark.parametrize("template", TEMPLATE_FILES, ids=lambda p: p.stem)
async def test_template_maps_fixture_to_expected(config_dir: Path, template: Path) -> None:
    doc = _load(config_dir, template)
    info = doc.metadata.template
    assert info is not None
    fixtures = TEMPLATES / "fixtures" / template.stem
    expected = json.loads((fixtures / "expected.json").read_text())
    # Supply every placeholder: env vars get the fake base URL or a marker, secrets a token.
    env: dict[str, str] = {}
    for r in info.requires:
        if r.kind == "env":
            env[r.name] = BASE if "URL" in r.name.upper() else f"value-{r.name}"
        else:
            env[f"HUD_SECRET_{r.name.upper()}"] = "fixture-token"
    ctx = ProviderContext.create(
        doc.metadata.name, SecretResolver(config_dir, config_dir / "none", env=env), env
    )
    provider = build_declarative(doc, ctx)
    await provider.startup()
    try:
        with respx.mock(assert_all_called=True) as mock:
            for res in doc.spec.resources:
                body = json.loads((fixtures / f"{res.name}.json").read_text())
                mock.route(method=res.request.method, path=res.request.path).mock(
                    return_value=httpx.Response(200, json=body)
                )
                result = await provider.poll(res.name)
                want = expected[res.name]
                assert {r.uid: r.state.value for r in result.resources} == want["resources"]
                got = {f"{m.resource_uid}/{m.name}": m.value for m in result.metrics}
                units = {f"{m.resource_uid}/{m.name}": m.unit.value for m in result.metrics}
                assert got == pytest.approx({k: v[0] for k, v in want["metrics"].items()})
                assert units == {k: v[1] for k, v in want["metrics"].items()}
    finally:
        await provider.shutdown()


# ----------------------------------------------------------------------- plex specifics


def _plex_provider(config_dir: Path) -> Any:
    doc = _load(config_dir, TEMPLATES / "plex.yaml")
    env = {"PLEX_BASE_URL": BASE, "HUD_SECRET_PLEX_TOKEN": "fixture-token"}
    ctx = ProviderContext.create(
        "plex", SecretResolver(config_dir, config_dir / "none", env=env), env
    )
    return build_declarative(doc, ctx)


@pytest.mark.skipif(not (TEMPLATES / "plex.yaml").is_file(), reason="plex template not bundled")
async def test_plex_negotiates_json_and_tolerates_an_idle_server(config_dir: Path) -> None:
    """Plex answers XML unless asked for JSON, and an idle server returns a MediaContainer
    with no Metadata key at all — which must be an empty poll, not a failed provider."""
    provider = _plex_provider(config_dir)
    await provider.startup()
    try:
        with respx.mock(assert_all_called=True) as mock:
            route = mock.get(f"{BASE}/status/sessions").mock(
                return_value=httpx.Response(200, json={"MediaContainer": {"size": 0}})
            )
            result = await provider.poll("sessions")
        request = route.calls[0].request
        assert request.headers["accept"] == "application/json"
        assert request.headers["x-plex-token"] == "fixture-token"
        assert result.resources == [] and result.metrics == []
    finally:
        await provider.shutdown()


# ------------------------------------------------------------------- *arr queue collisions


async def test_two_queue_rows_for_one_episode_both_show(
    config_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Found live: Sonarr listed one episode twice (a failed download and a new grab). The
    queue keys on the episode so a pending item keeps its history when grabbed; the second
    row now shows under its download id (or, still pending, its release name) instead of being
    dropped with a warning every poll."""
    doc = _load(config_dir, TEMPLATES / "sonarr.yaml")
    env = {"SONARR_BASE_URL": BASE, "HUD_SECRET_SONARR_API_KEY": "fixture-token"}
    ctx = ProviderContext.create(
        "sonarr", SecretResolver(config_dir, config_dir / "none", env=env), env
    )
    body = json.loads((TEMPLATES / "fixtures" / "sonarr" / "queue.json").read_text())
    failed = next(r for r in body["records"] if r["episodeId"] == 1105)
    body["records"].append(
        {
            **failed,
            "id": 104,
            "downloadId": "NEWGRAB",
            "status": "downloading",
            "trackedDownloadStatus": "ok",
            "trackedDownloadState": "downloading",
            "statusMessages": [],
            "errorMessage": None,
        }
    )
    pending = next(r for r in body["records"] if r["episodeId"] == 2101)
    body["records"].append({**pending, "id": 205})
    provider = build_declarative(doc, ctx)
    await provider.startup()
    try:
        with respx.mock() as mock:
            mock.get(f"{BASE}/api/v3/queue").mock(return_value=httpx.Response(200, json=body))
            result = await provider.poll("queue")
    finally:
        await provider.shutdown()
    states = {r.uid: r.state.value for r in result.resources}
    assert states["sonarr:download:ep-1105"] == "down"  # the first row keeps the episode uid
    assert states["sonarr:download:dl-NEWGRAB"] == "up"
    assert any(u.startswith("sonarr:download:rel-") for u in states)
    assert len(result.resources) == 9
    assert "duplicate uid" not in caplog.text


def test_a_collision_uid_is_held_to_the_stable_uid_rule(config_dir: Path) -> None:
    """uid_on_collision is checked like uid (invariant 8): a queue row's id is volatile."""
    text = (
        (TEMPLATES / "sonarr.yaml")
        .read_text()
        .replace("else ('rel-' ~ item.title) if item.title", "else ('q-' ~ item.id) if item.id", 1)
    )
    (config_dir / "settings.yaml").write_text("apiVersion: hud/v1\nkind: Settings\n")
    (config_dir / "providers").mkdir(exist_ok=True)
    (config_dir / "providers" / "sonarr.yaml").write_text(text)
    snap = ConfigManager(config_dir).load()
    assert any("uid_on_collision" in str(q) and "volatile" in str(q) for q in snap.quarantined)
