# SPDX-License-Identifier: Apache-2.0
"""Registry reconciliation: unchanged providers keep running, changed ones are rebuilt,
a provider that cannot start is recorded and never blocks the others."""

from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from hud.config import ConfigManager, SecretResolver
from hud.config.schemas import ProviderDocument
from hud.providers import (
    PollGroup,
    PollResult,
    Provider,
    ProviderBuildError,
    ProviderContext,
    ProviderRegistry,
    ProviderTier,
    RegistryDiff,
)

SETTINGS = "apiVersion: hud/v1\nkind: Settings\n"


def provider_yaml(name: str, interval: str = "30s") -> str:
    return f"""\
apiVersion: hud/v1
kind: Provider
metadata:
  name: {name}
spec:
  transport:
    base_url: http://{name}.lab
  defaults:
    interval: {interval}
  resources:
    - name: things
      request: {{ path: /api/things }}
      map:
        uid: "{name}:thing:{{{{ item.name }}}}"
        kind: thing
        name: "{{{{ item.name }}}}"
"""


class FakeProvider(Provider):
    tier = ProviderTier.DECLARATIVE
    instances: ClassVar[list["FakeProvider"]] = []

    def __init__(self, ctx: ProviderContext, doc: ProviderDocument) -> None:
        super().__init__(ctx, doc.metadata.labels)
        self.doc = doc
        self.started = False
        self.stopped = False
        FakeProvider.instances.append(self)

    def groups(self) -> Sequence[PollGroup]:
        return [PollGroup("things", interval=self.doc.spec.defaults.interval_seconds)]

    async def startup(self) -> None:
        if self.name == "boom":
            msg = "cannot reach anything"
            raise ProviderBuildError(self.name, msg)
        if self.name == "bug":
            raise RuntimeError("unexpected")
        self.started = True

    async def poll(self, group: str) -> PollResult:
        return PollResult()

    async def shutdown(self) -> None:
        self.stopped = True


@pytest.fixture
def registry(config_dir: Path) -> ProviderRegistry:
    FakeProvider.instances = []
    secrets = SecretResolver(config_dir, secrets_dir=config_dir / "nosecrets", env={})

    def factory(doc: ProviderDocument, ctx: ProviderContext) -> Provider:
        return FakeProvider(ctx, doc)

    return ProviderRegistry(
        factory=factory,
        context_factory=lambda name: ProviderContext.create(name, secrets, {}),
    )


def _write(config_dir: Path, files: dict[str, str]) -> None:
    (config_dir / "settings.yaml").write_text(SETTINGS)
    d = config_dir / "providers"
    d.mkdir(exist_ok=True)
    for p in d.glob("*.yaml"):
        p.unlink()
    for name, text in files.items():
        (d / f"{name}.yaml").write_text(text)


async def test_apply_adds_then_is_idempotent(
    registry: ProviderRegistry, manager: ConfigManager, config_dir: Path
) -> None:
    _write(config_dir, {"a": provider_yaml("a"), "b": provider_yaml("b")})
    diff = await registry.apply(manager.load())
    assert diff == RegistryDiff(added=("a", "b"))
    assert set(registry.providers) == {"a", "b"}
    assert all(p.started for p in FakeProvider.instances)
    assert (await registry.apply(manager.load())).empty
    assert len(FakeProvider.instances) == 2  # nothing rebuilt


async def test_changed_document_is_rebuilt_and_old_instance_stopped(
    registry: ProviderRegistry, manager: ConfigManager, config_dir: Path
) -> None:
    _write(config_dir, {"a": provider_yaml("a")})
    await registry.apply(manager.load())
    first = registry.get("a")
    assert isinstance(first, FakeProvider)
    _write(config_dir, {"a": provider_yaml("a", interval="1m")})
    diff = await registry.apply(manager.load())
    assert diff == RegistryDiff(replaced=("a",))
    assert first.stopped
    second = registry.get("a")
    assert second is not first and second is not None
    assert second.group("things").interval == 60


async def test_removed_document_shuts_provider_down(
    registry: ProviderRegistry, manager: ConfigManager, config_dir: Path
) -> None:
    _write(config_dir, {"a": provider_yaml("a"), "b": provider_yaml("b")})
    await registry.apply(manager.load())
    b = registry.get("b")
    assert isinstance(b, FakeProvider)
    _write(config_dir, {"a": provider_yaml("a")})
    diff = await registry.apply(manager.load())
    assert diff == RegistryDiff(removed=("b",))
    assert b.stopped
    assert set(registry.providers) == {"a"}


async def test_build_failure_is_recorded_and_isolated(
    registry: ProviderRegistry, manager: ConfigManager, config_dir: Path
) -> None:
    _write(
        config_dir,
        {"a": provider_yaml("a"), "boom": provider_yaml("boom"), "bug": provider_yaml("bug")},
    )
    diff = await registry.apply(manager.load())
    assert diff == RegistryDiff(added=("a",), failed=("boom", "bug"))
    assert set(registry.providers) == {"a"}
    assert registry.failures == {
        "boom": "cannot reach anything",
        "bug": "RuntimeError: unexpected",
    }
    # Same broken document again: not retried, not re-logged, not reported as a change.
    assert (await registry.apply(manager.load())).empty
    # Fixing the document (any content change) triggers a fresh build attempt.
    _write(config_dir, {"a": provider_yaml("a"), "boom": provider_yaml("boom", "1m")})
    diff = await registry.apply(manager.load())
    assert diff.removed == ("bug",)
    assert diff.failed == ("boom",)


async def test_rebuild_one_provider_and_hooks(
    registry: ProviderRegistry, manager: ConfigManager, config_dir: Path
) -> None:
    seen: list[RegistryDiff] = []

    async def hook(diff: RegistryDiff) -> None:
        seen.append(diff)

    registry.on_diff(hook)
    _write(config_dir, {"a": provider_yaml("a")})
    snap = manager.load()
    await registry.apply(snap)
    first = registry.get("a")
    assert await registry.rebuild("a", snap) is True
    assert isinstance(first, FakeProvider) and first.stopped
    assert registry.get("a") is not first
    assert await registry.rebuild("nope", snap) is False
    assert seen == [RegistryDiff(added=("a",)), RegistryDiff(replaced=("a",))]


async def test_shutdown_stops_everything(
    registry: ProviderRegistry, manager: ConfigManager, config_dir: Path
) -> None:
    _write(config_dir, {"a": provider_yaml("a"), "b": provider_yaml("b")})
    await registry.apply(manager.load())
    await registry.shutdown()
    assert registry.providers == {}
    assert all(p.stopped for p in FakeProvider.instances)


def test_poll_group_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="interval/timeout"):
        PollGroup("x", interval=0)
    with pytest.raises(ValueError, match="jitter"):
        PollGroup("x", interval=1, jitter=-1)
