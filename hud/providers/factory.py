# SPDX-License-Identifier: Apache-2.0
"""Document → provider instance. The registry calls this for every ``kind: Provider``."""

from __future__ import annotations

from pydantic import ValidationError

from hud.config.interpolate import MissingEnvVarError, interpolate_env
from hud.config.schemas import ProviderDocument
from hud.config.secrets import SecretNotFoundError
from hud.providers.base import Provider, ProviderContext
from hud.providers.declarative import build_declarative
from hud.providers.errors import ProviderBuildError
from hud.providers.sdk import PluginProvider, Schedule
from hud.providers.sdk.loader import PluginLoader

DEFAULT_PLUGIN_TIMEOUT = 15.0


class ProviderFactory:
    def __init__(self, loader: PluginLoader) -> None:
        self.loader = loader

    def __call__(self, doc: ProviderDocument, ctx: ProviderContext) -> Provider:
        if doc.spec.plugin is None:
            return build_declarative(doc, ctx)
        return self.build_plugin(doc, ctx)

    def build_plugin(self, doc: ProviderDocument, ctx: ProviderContext) -> PluginProvider:
        plugin = doc.spec.plugin
        assert plugin is not None
        cls = self.loader.get(ctx.name, plugin)
        try:
            raw = ctx.secrets.resolve_refs(_interpolate(doc.spec.config, ctx))
            config = cls.config_model.model_validate(raw)
        except SecretNotFoundError as exc:
            raise ProviderBuildError(ctx.name, f"config: {exc}") from exc
        except MissingEnvVarError as exc:
            raise ProviderBuildError(ctx.name, f"config: {exc}") from exc
        except ValidationError as exc:
            lines = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'config'}: {e['msg']}"
                for e in exc.errors(include_url=False)
            )
            msg = f"config invalid for plugin {plugin!r}: {lines}"
            raise ProviderBuildError(ctx.name, msg) from exc
        defaults = doc.spec.defaults
        schedule = Schedule(
            interval=float(defaults.interval_seconds),
            jitter=float(defaults.jitter_seconds),
            timeout=DEFAULT_PLUGIN_TIMEOUT,
            discover_interval=cls.discover_interval,
        )
        provider = cls(ctx, config, schedule)
        provider.labels = dict(doc.metadata.labels)
        return provider


def _interpolate(value: object, ctx: ProviderContext) -> object:
    if isinstance(value, str):
        return interpolate_env(value, ctx.env)
    if isinstance(value, dict):
        return {k: _interpolate(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, ctx) for v in value]
    return value
