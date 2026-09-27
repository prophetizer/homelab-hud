# SPDX-License-Identifier: Apache-2.0
"""Application factory and lifespan: config → database → background tasks → serve."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from sqlalchemy import Engine

from hud import __version__
from hud.api import api_v1
from hud.api.auth import auth_error_response
from hud.api.images import ImageCache
from hud.api.spa import mount_spa
from hud.auth import AuthError, Authorizer, AuthService
from hud.collector import LiveCache, StoreWriter
from hud.collector.scheduler import Collector
from hud.config import ConfigError, ConfigManager, ConfigSnapshot, SecretResolver, redact
from hud.providers import ProviderContext, ProviderRegistry
from hud.providers.factory import ProviderFactory
from hud.providers.sdk.loader import PluginLoader
from hud.settings import HudEnv
from hud.store import StorePaths, create_store_engine, upgrade_all
from hud.widgets import WidgetEngine
from hud.widgets.icons import IconStore

log = logging.getLogger("hud")


LOG_FORMAT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, force=False)
    redact.install(LOG_FORMAT)


def _load_config(env: HudEnv) -> tuple[ConfigManager, ConfigSnapshot]:
    config = ConfigManager(env.config_dir, poll_interval=env.config_poll_interval)
    config.bootstrap()
    try:
        snap = config.load()
    except ConfigError as exc:
        # Refuse to start. Each line is file:line:col: message.
        log.error("configuration is invalid; refusing to start:\n%s", exc)
        raise
    log.info("config loaded from %s, version %s", env.config_dir, snap.version)
    return config, snap


async def _start_auth(engine: Engine, secrets: SecretResolver, snap: ConfigSnapshot) -> AuthService:
    auth = AuthService(engine, secrets, snap.settings.spec.auth, Authorizer(snap.rbac.spec))
    log.info("auth backends: %s", ", ".join(snap.settings.spec.auth.backends))
    if await auth.setup_required():
        log.warning("no local account exists yet; open the UI to create the admin account")
    purged = await auth.purge_expired()
    if purged:
        log.info("purged %d expired sessions", purged)
    return auth


def create_app(env: HudEnv | None = None) -> FastAPI:
    env = env or HudEnv()
    configure_logging(env.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.env = env
        app.state.started_at = datetime.now(UTC)
        app.state.started_monotonic = time.monotonic()

        config, snap = _load_config(env)
        app.state.config = config

        paths = StorePaths(env.data_dir)
        app.state.db_revisions = await asyncio.to_thread(upgrade_all, paths)
        app.state.store_paths = paths
        app.state.engine = create_store_engine(paths)

        # Providers: registry builds instances from config, collector owns their time.
        secrets = SecretResolver(env.config_dir)
        environ = dict(os.environ)
        registry = ProviderRegistry(
            factory=ProviderFactory(PluginLoader(env.config_dir / "plugins")),
            context_factory=lambda name: ProviderContext.create(name, secrets, environ),
        )
        cache = LiveCache()
        writer = StoreWriter(app.state.engine)
        collector = Collector(registry, cache, sinks=[writer])
        app.state.registry = registry
        app.state.cache = cache
        app.state.collector = collector
        app.state.writer = writer
        app.state.widgets = WidgetEngine(cache, app.state.engine)
        app.state.icons = IconStore(env.data_dir / "icons")
        app.state.images = ImageCache()
        auth = await _start_auth(app.state.engine, secrets, snap)
        app.state.auth = auth
        await registry.apply(snap)

        async def reconcile(new_snapshot: ConfigSnapshot) -> None:
            await auth.apply(new_snapshot)
            await registry.apply(new_snapshot)

        config.on_reload(reconcile)
        collector.start()

        async with asyncio.TaskGroup() as tg:
            watcher = tg.create_task(config.watch(), name="config-watcher")
            log.info("HUD %s ready on port %d", __version__, env.port)
            try:
                yield
            finally:
                watcher.cancel()
                await collector.stop()
                await registry.shutdown()
        app.state.engine.dispose()

    app = FastAPI(
        title="HUD",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.include_router(api_v1)
    app.add_exception_handler(AuthError, lambda _req, exc: auth_error_response(exc))
    mount_spa(app, env.static_dir)
    return app


app = create_app()
