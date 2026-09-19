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

from hud import __version__
from hud.api import api_v1
from hud.api.spa import mount_spa
from hud.collector import LiveCache, StoreWriter
from hud.collector.scheduler import Collector
from hud.config import ConfigError, ConfigManager, ConfigSnapshot, SecretResolver
from hud.providers import ProviderContext, ProviderRegistry
from hud.providers.factory import build_provider
from hud.settings import HudEnv
from hud.store import StorePaths, create_store_engine, upgrade_all

log = logging.getLogger("hud")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        force=False,
    )


def create_app(env: HudEnv | None = None) -> FastAPI:
    env = env or HudEnv()
    configure_logging(env.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.env = env
        app.state.started_at = datetime.now(UTC)
        app.state.started_monotonic = time.monotonic()

        config = ConfigManager(env.config_dir, poll_interval=env.config_poll_interval)
        config.bootstrap()
        try:
            snap = config.load()
        except ConfigError as exc:
            # Refuse to start. Each line is file:line:col: message.
            log.error("configuration is invalid; refusing to start:\n%s", exc)
            raise
        log.info("config loaded from %s, version %s", env.config_dir, snap.version)
        app.state.config = config

        paths = StorePaths(env.data_dir)
        app.state.db_revisions = await asyncio.to_thread(upgrade_all, paths)
        app.state.store_paths = paths
        app.state.engine = create_store_engine(paths)

        # Providers: registry builds instances from config, collector owns their time.
        secrets = SecretResolver(env.config_dir)
        environ = dict(os.environ)
        registry = ProviderRegistry(
            factory=build_provider,
            context_factory=lambda name: ProviderContext.create(name, secrets, environ),
        )
        cache = LiveCache()
        writer = StoreWriter(app.state.engine)
        collector = Collector(registry, cache, sinks=[writer])
        app.state.registry = registry
        app.state.cache = cache
        app.state.collector = collector
        app.state.writer = writer
        await registry.apply(snap)

        async def reconcile(new_snapshot: ConfigSnapshot) -> None:
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
    mount_spa(app, env.static_dir)
    return app


app = create_app()
