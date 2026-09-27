# SPDX-License-Identifier: Apache-2.0
"""HTTP transport for declarative providers: spec → client, request → JSON pages.

Secrets and ``${VAR}`` references are resolved here, once, at build time. The resolved
values live on the client object only; nothing here returns them and nothing logs them.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from hud.config.interpolate import MissingEnvVarError, interpolate_env
from hud.config.schemas.provider import (
    AuthApiKey,
    AuthBasic,
    AuthBearer,
    AuthHeader,
    AuthQuery,
    Paginate,
    Request,
    Transport,
)
from hud.config.secrets import SecretNotFoundError, secret_name
from hud.providers.base import HttpOptions, ProviderContext
from hud.providers.declarative.expr import Expr, ExprError, new_environment
from hud.providers.declarative.select import Selector
from hud.providers.errors import ProviderBuildError, ProviderPollError

_PARAM_ENV = new_environment()

MAX_BODY_BYTES = 32 * 1024 * 1024  # a homelab API returning more than this is a bug


def build_http_options(ctx: ProviderContext, spec: Transport) -> HttpOptions:
    """Resolve env and secrets into :class:`HttpOptions`. Fails the build, not the poll."""
    try:
        base_url = interpolate_env(spec.base_url, ctx.env)
        headers: dict[str, str] = {"Accept": "application/json"}
        params: dict[str, str] = {}
        auth: httpx.Auth | None = None
        match spec.auth:
            case AuthBearer(token=ref):
                headers["Authorization"] = f"Bearer {ctx.secrets.resolve(secret_name(ref))}"
            case AuthApiKey(header=header, key=ref):
                headers[header] = ctx.secrets.resolve(secret_name(ref))
            case AuthBasic(username=user, password=ref):
                auth = httpx.BasicAuth(user, ctx.secrets.resolve(secret_name(ref)))
            case AuthHeader(name=name, value=ref):
                headers[name] = ctx.secrets.resolve(secret_name(ref))
            case AuthQuery(param=param, value=ref):
                params[param] = ctx.secrets.resolve(secret_name(ref))
            case _:
                pass
    except MissingEnvVarError as exc:
        raise ProviderBuildError(ctx.name, f"transport: {exc}") from exc
    except SecretNotFoundError as exc:
        raise ProviderBuildError(ctx.name, f"transport: {exc}") from exc
    if not base_url.startswith(("http://", "https://")):
        msg = f"transport: base_url {base_url!r} is not an absolute http(s) URL"
        raise ProviderBuildError(ctx.name, msg)
    return HttpOptions(
        base_url=base_url,
        timeout=float(spec.timeout_seconds),
        verify_tls=spec.verify_tls,
        headers=headers,
        auth=auth,
        max_connections=spec.max_connections,
        params=params,
    )


@dataclass(frozen=True)
class CompiledRequest:
    method: str
    path: str
    params: dict[str, str]
    headers: dict[str, str]
    body: dict[str, Any] | None
    paginate: Paginate | None
    cursor: Selector | None
    # Param values holding ``{{ … }}``, rendered at each poll with no item in scope — for a
    # moving window such as a calendar's ``start``/``end`` (``utcnow(days=14)``).
    dynamic: dict[str, Expr] = field(default_factory=dict)

    def render_params(self, provider: str) -> dict[str, str]:
        if not self.dynamic:
            return self.params
        out = dict(self.params)
        for k, expr in self.dynamic.items():
            try:
                out[k] = expr.render_str()
            except ExprError as exc:
                raise ProviderPollError(
                    provider, f"{self.method} {self.path}: param {k}: {exc}"
                ) from exc
        return out

    @classmethod
    def build(
        cls, ctx: ProviderContext, req: Request, paginate: Paginate | None
    ) -> CompiledRequest:
        try:
            path = interpolate_env(req.path, ctx.env)
            params = {k: interpolate_env(v, ctx.env) for k, v in req.params.items()}
            headers = {k: interpolate_env(v, ctx.env) for k, v in req.headers.items()}
        except MissingEnvVarError as exc:
            raise ProviderBuildError(ctx.name, f"request {req.path!r}: {exc}") from exc
        cursor = Selector(paginate.cursor_path) if paginate and paginate.cursor_path else None
        dynamic: dict[str, Expr] = {}
        for k, v in list(params.items()):
            if "{{" in v or "{%" in v:
                try:
                    dynamic[k] = Expr(_PARAM_ENV, v)
                except ExprError as exc:
                    raise ProviderBuildError(
                        ctx.name, f"request {req.path!r}: param {k}: {exc}"
                    ) from exc
                del params[k]
        return cls(req.method, path, params, headers, req.body, paginate, cursor, dynamic)


async def fetch_pages(
    client: httpx.AsyncClient, provider: str, req: CompiledRequest
) -> AsyncIterator[Any]:
    """Yield each page's parsed JSON. Without ``paginate`` that is exactly one page.

    The caller decides when a page is empty (it owns ``select``) and stops iterating; for
    ``cursor`` style the generator also stops when the response carries no next cursor."""
    pg = req.paginate
    if pg is None:
        yield await _fetch_json(client, provider, req, req.render_params(provider))
        return
    cursor: Any = None
    for page_no in range(pg.max_pages):
        params = req.render_params(provider) | {}
        if pg.style == "page":
            params[pg.param] = str(page_no + 1)
        elif pg.style == "offset":
            params[pg.param] = str(page_no * pg.size)
            params[pg.size_param or "limit"] = str(pg.size)
        elif cursor is not None:
            params[pg.param] = str(cursor)
        elif page_no > 0:
            return
        data = await _fetch_json(client, provider, req, params)
        yield data
        if pg.style == "cursor":
            cursor = req.cursor.first(data) if req.cursor else None
            if cursor in (None, ""):
                return


async def _fetch_json(
    client: httpx.AsyncClient, provider: str, req: CompiledRequest, params: Mapping[str, str]
) -> Any:  # noqa: ANN401
    try:
        resp = await client.request(
            req.method, req.path, params=dict(params), headers=req.headers, json=req.body
        )
    except httpx.TimeoutException as exc:
        msg = f"{req.method} {req.path}: timed out after {client.timeout.read}s"
        raise ProviderPollError(provider, msg) from exc
    except httpx.HTTPError as exc:
        raise ProviderPollError(provider, f"{req.method} {req.path}: {exc}") from exc
    if resp.status_code >= 300:
        msg = f"{req.method} {req.path}: HTTP {resp.status_code} {resp.reason_phrase}"
        raise ProviderPollError(provider, msg)
    if len(resp.content) > MAX_BODY_BYTES:
        msg = f"{req.method} {req.path}: response exceeds {MAX_BODY_BYTES} bytes"
        raise ProviderPollError(provider, msg)
    try:
        return resp.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        ctype = resp.headers.get("content-type", "?")
        msg = f"{req.method} {req.path}: response is not JSON (content-type {ctype})"
        raise ProviderPollError(provider, msg) from exc
