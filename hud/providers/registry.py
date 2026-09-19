# SPDX-License-Identifier: Apache-2.0
"""Provider Registry (PLAN.md §4.1): build providers from a config snapshot, keep them
alive across reloads, and rebuild only what changed.

The registry owns *instances*; the scheduler owns *time*. On every snapshot the registry
diffs provider documents by content: unchanged providers keep running, changed ones are
shut down and rebuilt, removed ones are shut down. A provider that fails to build is
recorded with its error and shows as ``error`` in health — one bad provider never blocks
the others (invariant 6).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from hud.config import ConfigSnapshot
from hud.config.schemas import ProviderDocument
from hud.providers.base import Provider, ProviderContext
from hud.providers.errors import ProviderBuildError

log = logging.getLogger(__name__)

# Builds a Provider from its validated document. Raises ProviderBuildError on failure.
ProviderFactory = Callable[[ProviderDocument, ProviderContext], Provider]
ContextFactory = Callable[[str], ProviderContext]
DiffHook = Callable[["RegistryDiff"], Awaitable[None]]


@dataclass(frozen=True)
class RegistryDiff:
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.replaced or self.failed)


@dataclass
class _Entry:
    provider: Provider
    spec_hash: str


@dataclass
class _Failure:
    error: str
    spec_hash: str


@dataclass
class ProviderRegistry:
    factory: ProviderFactory
    context_factory: ContextFactory
    _entries: dict[str, _Entry] = field(default_factory=dict)
    _failures: dict[str, _Failure] = field(default_factory=dict)
    _hooks: list[DiffHook] = field(default_factory=list)

    # ------------------------------------------------------------------ read side

    @property
    def providers(self) -> dict[str, Provider]:
        return {name: e.provider for name, e in self._entries.items()}

    @property
    def failures(self) -> dict[str, str]:
        """Providers whose document is valid but which could not be instantiated."""
        return {name: f.error for name, f in self._failures.items()}

    def get(self, name: str) -> Provider | None:
        entry = self._entries.get(name)
        return entry.provider if entry else None

    def on_diff(self, hook: DiffHook) -> None:
        self._hooks.append(hook)

    # ------------------------------------------------------------------ apply snapshots

    async def apply(self, snapshot: ConfigSnapshot) -> RegistryDiff:
        """Reconcile instances with ``snapshot``. Safe to call on every reload."""
        wanted = {
            d.model.metadata.name: (d.model, _hash(d.model))
            for d in snapshot.documents
            if isinstance(d.model, ProviderDocument)
        }
        added: list[str] = []
        removed: list[str] = []
        replaced: list[str] = []
        failed: list[str] = []

        for name in sorted(set(self._entries) | set(self._failures)):
            if name not in wanted:
                await self._drop(name)
                removed.append(name)

        for name, (doc, spec_hash) in wanted.items():
            current = self._entries.get(name)
            failure = self._failures.get(name)
            if current and current.spec_hash == spec_hash:
                continue
            if failure and failure.spec_hash == spec_hash:
                continue  # same broken document; do not spam the log on every poll
            was_present = current is not None or failure is not None
            await self._drop(name)
            if await self._build(name, doc, spec_hash):
                (replaced if was_present else added).append(name)
            else:
                failed.append(name)

        diff = RegistryDiff(tuple(added), tuple(removed), tuple(replaced), tuple(failed))
        if not diff.empty:
            log.info(
                "providers reconciled: +%d -%d ~%d !%d",
                len(added),
                len(removed),
                len(replaced),
                len(failed),
            )
            for hook in self._hooks:
                try:
                    await hook(diff)
                except Exception:
                    log.exception("registry diff hook %r failed", hook)
        return diff

    async def rebuild(self, name: str, snapshot: ConfigSnapshot) -> bool:
        """Force-rebuild one provider from ``snapshot`` (``POST /providers/{name}/reload``).
        Returns False when the snapshot has no such provider or the build failed."""
        for d in snapshot.documents:
            if isinstance(d.model, ProviderDocument) and d.model.metadata.name == name:
                await self._drop(name)
                ok = await self._build(name, d.model, _hash(d.model))
                diff = RegistryDiff(replaced=(name,)) if ok else RegistryDiff(failed=(name,))
                for hook in self._hooks:
                    await hook(diff)
                return ok
        return False

    async def shutdown(self) -> None:
        for name in list(self._entries):
            await self._drop(name)

    # ------------------------------------------------------------------ internals

    async def _build(self, name: str, doc: ProviderDocument, spec_hash: str) -> bool:
        ctx = self.context_factory(name)
        try:
            provider = self.factory(doc, ctx)
            await provider.startup()
        except ProviderBuildError as exc:
            self._failures[name] = _Failure(exc.message, spec_hash)
            log.error("provider %s not started: %s", name, exc.message)
            return False
        except Exception as exc:
            # Anything else is a bug or an environment surprise; still never fatal.
            self._failures[name] = _Failure(f"{type(exc).__name__}: {exc}", spec_hash)
            log.exception("provider %s failed to start", name)
            return False
        self._entries[name] = _Entry(provider, spec_hash)
        self._failures.pop(name, None)
        log.info("provider %s started (%s, %d groups)", name, provider.tier, len(provider.groups()))
        return True

    async def _drop(self, name: str) -> None:
        self._failures.pop(name, None)
        entry = self._entries.pop(name, None)
        if entry is None:
            return
        try:
            await entry.provider.shutdown()
        except Exception:
            log.exception("provider %s raised during shutdown", name)


def _hash(doc: ProviderDocument) -> str:
    return hashlib.sha256(doc.model_dump_json(by_alias=True).encode()).hexdigest()[:16]
