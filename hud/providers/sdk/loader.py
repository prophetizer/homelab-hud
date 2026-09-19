# SPDX-License-Identifier: Apache-2.0
"""Find plugin classes: packaged ones via the ``hud.providers`` entry-point group, drop-ins
from ``/config/plugins/<name>/provider.py`` (PLAN.md §7.2).

A plugin that cannot be imported, is not registered under the expected name, or declares
an unsupported ``sdk_version`` yields a :class:`ProviderBuildError` naming the reason; the
registry shows it as ``error`` in health instead of crashing the process.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from importlib.metadata import entry_points
from pathlib import Path

from hud.providers.errors import ProviderBuildError
from hud.providers.sdk import SUPPORTED_SDK_MAJORS, PluginProvider

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "hud.providers"
DROPIN_FILE = "provider.py"
DROPIN_MODULE_PREFIX = "hud_dropin_"


class PluginLoader:
    def __init__(self, plugins_dir: Path | None = None) -> None:
        self.plugins_dir = plugins_dir
        self._classes: dict[str, type[PluginProvider]] = {}

    def add(self, cls: type[PluginProvider]) -> None:
        """Register a class directly (tests, embedded use)."""
        self._classes[cls.plugin_name] = cls

    def available(self) -> list[str]:
        names = set(self._classes)
        names.update(ep.name for ep in entry_points(group=ENTRY_POINT_GROUP))
        if self.plugins_dir and self.plugins_dir.is_dir():
            names.update(p.name for p in self.plugins_dir.iterdir() if (p / DROPIN_FILE).is_file())
        return sorted(names)

    def get(self, provider: str, plugin: str) -> type[PluginProvider]:
        """The class for ``plugin``. ``provider`` is only for error messages."""
        cls = self._classes.get(plugin)
        if cls is None:
            cls = self._load_dropin(provider, plugin) or self._load_entry_point(provider, plugin)
        if cls is None:
            known = ", ".join(self.available()) or "none"
            msg = f"unknown plugin {plugin!r} (available: {known})"
            raise ProviderBuildError(provider, msg)
        _check_sdk_version(provider, cls)
        self._classes[plugin] = cls
        return cls

    # ------------------------------------------------------------------ sources

    def _load_entry_point(self, provider: str, plugin: str) -> type[PluginProvider] | None:
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            if ep.name != plugin:
                continue
            try:
                cls = ep.load()
            except Exception as exc:
                msg = f"plugin {plugin!r} failed to import: {type(exc).__name__}: {exc}"
                raise ProviderBuildError(provider, msg) from exc
            return _validate_class(provider, plugin, cls)
        return None

    def _load_dropin(self, provider: str, plugin: str) -> type[PluginProvider] | None:
        if self.plugins_dir is None:
            return None
        path = self.plugins_dir / plugin / DROPIN_FILE
        if not path.is_file():
            return None
        module_name = DROPIN_MODULE_PREFIX + plugin.replace("-", "_")
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ProviderBuildError(provider, f"cannot load plugin file {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(module_name, None)
            msg = f"plugin file {path} failed to import: {type(exc).__name__}: {exc}"
            raise ProviderBuildError(provider, msg) from exc
        candidates = [
            obj
            for obj in vars(module).values()
            if isinstance(obj, type)
            and issubclass(obj, PluginProvider)
            and obj is not PluginProvider
            and getattr(obj, "plugin_name", None) == plugin
        ]
        if not candidates:
            msg = f"{path} defines no class registered as {plugin!r} (use @register)"
            raise ProviderBuildError(provider, msg)
        log.info("loaded drop-in plugin %s from %s", plugin, path)
        return candidates[0]


def _validate_class(provider: str, plugin: str, cls: object) -> type[PluginProvider]:
    if not (isinstance(cls, type) and issubclass(cls, PluginProvider)):
        msg = f"plugin {plugin!r} entry point is not a PluginProvider subclass"
        raise ProviderBuildError(provider, msg)
    if getattr(cls, "plugin_name", None) != plugin:
        registered = getattr(cls, "plugin_name", None)
        msg = f"plugin {plugin!r} entry point is registered as {registered!r}"
        raise ProviderBuildError(provider, msg)
    return cls


def _check_sdk_version(provider: str, cls: type[PluginProvider]) -> None:
    declared = getattr(cls, "sdk_version", None)
    try:
        major = int(str(declared).split(".", 1)[0])
    except ValueError:
        msg = f"plugin {cls.plugin_name!r} declares an invalid sdk_version {declared!r}"
        raise ProviderBuildError(provider, msg) from None
    if major not in SUPPORTED_SDK_MAJORS:
        supported = ", ".join(str(m) for m in sorted(SUPPORTED_SDK_MAJORS))
        msg = (
            f"plugin {cls.plugin_name!r} needs SDK {declared}; this build supports "
            f"major version(s) {supported}"
        )
        raise ProviderBuildError(provider, msg)
