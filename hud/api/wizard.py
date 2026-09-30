# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/wizard`` — Connect a service (PLAN §8.5, round 3).

Admin work (``providers:edit``): it writes a provider file and a credential. Credentials
arrive as request fields, are never logged or echoed back, go only to the secret store, and
the audit row names them without their values.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr

from hud.api import deps
from hud.config.errors import ConfigError
from hud.config.schemas.provider import ProviderDocument
from hud.config.secret_store import store_secret
from hud.config.secrets import SecretResolver
from hud.widgets.icons import canonical as canonical_icon
from hud.wizard import Template, discover, guard_url, load_catalog, render, try_connection

router = APIRouter(tags=["wizard"])
PERMISSION = "providers:edit"


class Connection(BaseModel):
    template: str
    values: dict[str, str] = Field(default_factory=dict)  # plain placeholders: the base URL
    secrets: dict[str, SecretStr] = Field(default_factory=dict)  # typed credentials


def _catalog(request: Request) -> dict[str, Template]:
    cached = getattr(request.app.state, "wizard_catalog", None)
    if cached is None:
        cached = load_catalog()
        request.app.state.wizard_catalog = cached
    return cached


def _secrets(request: Request) -> SecretResolver:
    return request.app.state.secrets  # type: ignore[no-any-return]


def _connected(request: Request) -> set[str]:
    return {
        d.model.metadata.name
        for d in deps.config(request).snapshot.documents
        if isinstance(d.model, ProviderDocument)
    }


@router.get("/wizard/catalog")
async def catalog(request: Request) -> dict[str, Any]:
    """Every bundled service, the fields connecting it needs, and whether each credential
    is already set (and where — never its value)."""
    await deps.require(request, PERMISSION)
    secrets = _secrets(request)
    connected = _connected(request)
    return {
        "templates": [
            {
                "name": t.name,
                "service": t.service,
                # The service's own icon, as its tiles show it (a brand icon by name).
                "icon": canonical_icon(t.name),
                "docs": t.docs,
                "port": t.port,
                "connected": t.name in connected,
                "fields": [
                    {
                        "name": r.name,
                        "kind": r.kind,
                        "hint": r.hint,
                        "set_in": secrets.source(r.name) if r.kind == "secret" else None,
                    }
                    for r in t.requires
                ],
            }
            for t in sorted(_catalog(request).values(), key=lambda t: t.service.lower())
        ]
    }


@router.get("/wizard/discover")
async def found(request: Request) -> dict[str, Any]:
    """Containers on this host a bundled template can connect (needs the Docker provider)."""
    await deps.require(request, PERMISSION)
    cache = request.app.state.cache
    return {"found": [asdict(f) for f in discover(cache, _catalog(request), _connected(request))]}


async def _prepare(request: Request, body: Connection) -> tuple[Template, str, dict[str, str]]:
    t = _catalog(request).get(body.template)
    if t is None:
        raise HTTPException(status_code=404, detail=f"no bundled template {body.template!r}")
    try:
        text = render(t, body.values)
        for r in t.env():
            if r.name.endswith("_URL"):
                await guard_url(body.values[r.name].strip(), request.app.state.env.port)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    known = {r.name for r in t.secrets()}
    typed = {k: v.get_secret_value() for k, v in body.secrets.items() if k in known}
    return t, text, typed


@router.post("/wizard/test")
async def test(request: Request, body: Connection) -> dict[str, Any]:
    """Poll the service once with what was typed; nothing is written."""
    await deps.require(request, PERMISSION)
    t, text, typed = await _prepare(request, body)
    missing = [
        r.name
        for r in t.secrets()
        if r.name not in typed and _secrets(request).source(r.name) is None
    ]
    if missing:
        raise HTTPException(status_code=422, detail=f"enter {', '.join(missing)}")
    return asdict(await try_connection(t, text, _secrets(request), typed))


@router.post("/wizard/connect", status_code=201)
async def connect(request: Request, body: Connection) -> dict[str, Any]:
    """Store the credentials, write ``providers/<name>.yaml``, reload. Refused (409) when
    that provider already exists — it is edited as YAML, not replaced."""
    p = await deps.require(request, PERMISSION)
    t, text, typed = await _prepare(request, body)
    config = deps.config(request)
    relative = f"providers/{t.name}.yaml"
    if (config.config_dir / relative).exists() or t.name in _connected(request):
        raise HTTPException(status_code=409, detail=f"{t.service} is already connected")
    secrets = _secrets(request)
    warnings: list[str] = []
    for r in t.secrets():
        source = secrets.source(r.name)
        if r.name not in typed:
            if source is None:
                raise HTTPException(status_code=422, detail=f"enter {r.name}")
            continue
        if source in ("docker secret", "environment"):
            warnings.append(f"{r.name}: a {source} of that name exists and is used instead")
            continue
        await asyncio.to_thread(store_secret, config.config_dir, r.name, typed[r.name])
    try:
        await config.create_async(relative, text)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=f"{t.service} is already connected") from exc
    except (ConfigError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store = deps.auth(request).store
    await asyncio.to_thread(
        store.audit,
        p.subject,
        "providers.connect",
        t.name,
        "ok",
        {"template": t.name, "secrets": sorted(typed), "file": relative},
    )
    return {"provider": t.name, "file": relative, "warnings": warnings}
