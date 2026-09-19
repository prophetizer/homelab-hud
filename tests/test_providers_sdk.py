# SPDX-License-Identifier: Apache-2.0
"""Plugin SDK, loader (entry points + drop-ins, sdk_version gate) and factory."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hud.config import ConfigError, ConfigManager, SecretResolver
from hud.config.schemas import ProviderDocument
from hud.providers import ProviderBuildError, ProviderContext
from hud.providers.factory import ProviderFactory
from hud.providers.sdk import (
    COLLECT_GROUP,
    DISCOVER_GROUP,
    Metric,
    PluginConfig,
    PluginProvider,
    PollResult,
    Resource,
    Schedule,
    State,
    Unit,
    register,
)
from hud.providers.sdk import loader as loader_mod
from hud.providers.sdk.loader import PluginLoader

SETTINGS = "apiVersion: hud/v1\nkind: Settings\n"


class EchoConfig(PluginConfig):
    base_url: str
    token: str = "none"
    count: int = 2


@register("echo", config_model=EchoConfig)
class EchoProvider(PluginProvider):
    def __init__(self, ctx: ProviderContext, config: EchoConfig, schedule: Schedule) -> None:
        super().__init__(ctx, config, schedule)
        self.cfg = config
        self.discovers = 0
        self.collects = 0

    async def discover(self) -> list[Resource]:
        self.discovers += 1
        now = datetime.now(UTC)
        return [
            Resource(
                uid=f"{self.name}:thing:r{i}",
                provider=self.name,
                kind="thing",
                name=f"r{i}",
                state=State.UP,
                fetched_at=now,
            )
            for i in range(self.cfg.count)
        ]

    async def collect(self, resources: list[Resource]) -> PollResult:
        self.collects += 1
        now = datetime.now(UTC)
        kept = resources[:1]  # pretend the second one vanished
        return PollResult(
            resources=kept,
            metrics=[
                Metric(resource_uid=r.uid, name="v", value=1.0, unit=Unit.COUNT, ts=now)
                for r in kept
            ],
        )


def plugin_doc(config_dir: Path, text: str) -> ProviderDocument:
    (config_dir / "settings.yaml").write_text(SETTINGS)
    (config_dir / "providers").mkdir(exist_ok=True)
    (config_dir / "providers" / "p.yaml").write_text(text)
    snap = ConfigManager(config_dir).load()
    (doc,) = [d.model for d in snap.documents if isinstance(d.model, ProviderDocument)]
    return doc


ECHO_DOC = """\
apiVersion: hud/v1
kind: Provider
metadata:
  name: e1
  labels: { category: test }
spec:
  plugin: echo
  defaults: { interval: 45s, jitter: 3s }
  config:
    base_url: ${ECHO_URL}
    token: ${secret:echo_token}
    count: 3
"""


def ctx_for(config_dir: Path, name: str, env: dict[str, str]) -> ProviderContext:
    secrets = SecretResolver(config_dir, secrets_dir=config_dir / "none", env=env)
    return ProviderContext.create(name, secrets, env)


@pytest.fixture
def factory() -> ProviderFactory:
    loader = PluginLoader()
    loader.add(EchoProvider)
    return ProviderFactory(loader)


# ---------------------------------------------------------------- schema


def test_plugin_document_validates_and_tiers_are_exclusive(config_dir: Path) -> None:
    doc = plugin_doc(config_dir, ECHO_DOC)
    assert doc.spec.is_plugin and doc.spec.plugin == "echo"
    assert doc.spec.config["count"] == 3
    with pytest.raises(ConfigError, match="transport/resources are Tier 1"):
        plugin_doc(config_dir, ECHO_DOC + "  transport: { base_url: http://x }\n")
    with pytest.raises(ConfigError, match="transport is required"):
        plugin_doc(config_dir, ECHO_DOC.replace("  plugin: echo\n", ""))
    with pytest.raises(ConfigError, match="plugin name must be lowercase"):
        plugin_doc(config_dir, ECHO_DOC.replace("plugin: echo", "plugin: Echo!"))


# ---------------------------------------------------------------- factory + SDK


async def test_factory_builds_plugin_with_resolved_config(
    factory: ProviderFactory, config_dir: Path
) -> None:
    doc = plugin_doc(config_dir, ECHO_DOC)
    env = {"ECHO_URL": "http://echo.lab", "HUD_SECRET_ECHO_TOKEN": "s3cret"}
    p = factory(doc, ctx_for(config_dir, "e1", env))
    assert isinstance(p, EchoProvider)
    assert (p.cfg.base_url, p.cfg.token, p.cfg.count) == ("http://echo.lab", "s3cret", 3)
    assert p.labels == {"category": "test"}
    groups = {g.name: g for g in p.groups()}
    assert groups[DISCOVER_GROUP].interval == 300 and groups[COLLECT_GROUP].interval == 45
    assert groups[COLLECT_GROUP].jitter == 3

    # collect before any discover triggers a discover first; then only collect runs.
    result = await p.poll(COLLECT_GROUP)
    assert (p.discovers, p.collects) == (1, 1)
    assert [r.uid for r in result.resources] == ["e1:thing:r0"]
    assert [m.resource_uid for m in result.metrics] == ["e1:thing:r0"]
    result = await p.poll(DISCOVER_GROUP)
    assert (p.discovers, p.collects) == (2, 1)
    assert len(result.resources) == 3 and result.metrics == []


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("    count: 3\n", "    count: many\n", "count: Input should be a valid integer"),
        ("    count: 3\n", "    colour: red\n", "colour: Extra inputs are not permitted"),
        ("    base_url: ${ECHO_URL}\n", "", "base_url: Field required"),
    ],
)
def test_invalid_config_names_the_field(
    factory: ProviderFactory, config_dir: Path, old: str, new: str, match: str
) -> None:
    doc = plugin_doc(config_dir, ECHO_DOC.replace(old, new))
    env = {"ECHO_URL": "http://echo.lab", "HUD_SECRET_ECHO_TOKEN": "x"}
    with pytest.raises(ProviderBuildError, match=match):
        factory(doc, ctx_for(config_dir, "e1", env))


def test_missing_env_and_secret_fail_the_build(factory: ProviderFactory, config_dir: Path) -> None:
    doc = plugin_doc(config_dir, ECHO_DOC)
    with pytest.raises(ProviderBuildError, match="'ECHO_URL' is not set"):
        factory(doc, ctx_for(config_dir, "e1", {"HUD_SECRET_ECHO_TOKEN": "x"}))
    with pytest.raises(ProviderBuildError, match="secret 'echo_token' not found"):
        factory(doc, ctx_for(config_dir, "e1", {"ECHO_URL": "http://x"}))


def test_unknown_plugin_lists_available(factory: ProviderFactory, config_dir: Path) -> None:
    doc = plugin_doc(config_dir, ECHO_DOC.replace("plugin: echo", "plugin: nope"))
    # Packaged plugins (entry points) are listed alongside anything added directly.
    with pytest.raises(ProviderBuildError, match=r"unknown plugin 'nope' \(available: .*echo"):
        factory(doc, ctx_for(config_dir, "e1", {}))


# ---------------------------------------------------------------- loader


DROPIN = """
from hud.providers.sdk import PluginConfig, PluginProvider, Resource, State, register
from datetime import UTC, datetime

class Cfg(PluginConfig):
    greeting: str = "hi"

@register("dropin", config_model=Cfg{sdk})
class DropinProvider(PluginProvider):
    async def discover(self):
        return [Resource(uid=f"{{self.name}}:x:one", provider=self.name, kind="x",
                         name=self.config.greeting, state=State.UP,
                         fetched_at=datetime.now(UTC))]
"""


def _dropin(config_dir: Path, name: str, body: str) -> PluginLoader:
    d = config_dir / "plugins" / name
    d.mkdir(parents=True)
    (d / "provider.py").write_text(body)
    return PluginLoader(config_dir / "plugins")


async def test_dropin_plugin_loads_from_config_dir(config_dir: Path) -> None:
    loader = _dropin(config_dir, "dropin", DROPIN.format(sdk=""))
    assert "dropin" in loader.available()
    cls = loader.get("d1", "dropin")
    assert cls.plugin_name == "dropin" and loader.get("d1", "dropin") is cls  # cached
    doc = plugin_doc(
        config_dir,
        "apiVersion: hud/v1\nkind: Provider\nmetadata: {name: d1}\n"
        "spec: {plugin: dropin, config: {greeting: hello}}\n",
    )
    p = ProviderFactory(loader)(doc, ctx_for(config_dir, "d1", {}))
    result = await p.poll(DISCOVER_GROUP)
    assert [(r.uid, r.name) for r in result.resources] == [("d1:x:one", "hello")]


def test_dropin_with_wrong_sdk_major_is_refused(config_dir: Path) -> None:
    loader = _dropin(config_dir, "dropin", DROPIN.format(sdk=', sdk_version="2.0"'))
    with pytest.raises(ProviderBuildError, match=r"needs SDK 2\.0; this build supports"):
        loader.get("d1", "dropin")


def test_dropin_import_error_is_reported(config_dir: Path) -> None:
    loader = _dropin(config_dir, "broken", "import nosuchmodule_xyz\n")
    with pytest.raises(ProviderBuildError, match="failed to import: ModuleNotFoundError"):
        loader.get("b1", "broken")


def test_dropin_without_register_is_reported(config_dir: Path) -> None:
    loader = _dropin(config_dir, "quiet", "x = 1\n")
    with pytest.raises(ProviderBuildError, match="defines no class registered as 'quiet'"):
        loader.get("q1", "quiet")


class _FakeEntryPoint:
    def __init__(self, name: str, obj: object) -> None:
        self.name = name
        self._obj = obj

    def load(self) -> object:
        if isinstance(self._obj, Exception):
            raise self._obj
        return self._obj


def test_entry_point_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    eps = [
        _FakeEntryPoint("echo", EchoProvider),
        _FakeEntryPoint("notaplugin", object),
        _FakeEntryPoint("exploding", RuntimeError("kaboom")),
    ]
    monkeypatch.setattr(loader_mod, "entry_points", lambda group: eps)
    loader = PluginLoader()
    assert loader.available() == ["echo", "exploding", "notaplugin"]
    assert loader.get("p", "echo") is EchoProvider
    with pytest.raises(ProviderBuildError, match="not a PluginProvider subclass"):
        loader.get("p", "notaplugin")
    with pytest.raises(ProviderBuildError, match="failed to import: RuntimeError: kaboom"):
        loader.get("p", "exploding")
    # Registered under a different name than the entry point advertises.
    alias = [_FakeEntryPoint("alias", EchoProvider)]
    monkeypatch.setattr(loader_mod, "entry_points", lambda group: alias)
    with pytest.raises(ProviderBuildError, match="registered as 'echo'"):
        PluginLoader().get("p", "alias")


def test_register_rejects_non_plugin_classes() -> None:
    with pytest.raises(TypeError, match="must subclass PluginProvider"):
        register("bad", config_model=PluginConfig)(object)  # type: ignore[arg-type]
