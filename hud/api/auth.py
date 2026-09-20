# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/auth/*`` — who am I, log in, log out, first-run setup, user management.

Not in the plan's Appendix A; recorded as a Phase 1b amendment in PLAN.md §12.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from hud.api import deps
from hud.auth import ADMIN_GROUP, AuthError, Principal
from hud.auth.service import is_https
from hud.auth.store import User

router = APIRouter(prefix="/auth", tags=["auth"])

NEXT_COOKIE = "hud_oidc_next"
USERS_MANAGE = "users:manage"


# ----------------------------------------------------------------------------- payloads


class Backends(BaseModel):
    backends: list[str]
    setup_required: bool  # local enabled and no local account yet → show the setup screen
    registration: bool


class Me(BaseModel):
    subject: str
    display_name: str
    groups: list[str]
    source: Literal["local", "forward", "oidc"]
    permissions: list[str]
    csrf_token: str

    @classmethod
    def of(cls, p: Principal) -> Me:
        return cls(
            subject=p.subject,
            display_name=p.display_name,
            groups=sorted(p.groups),
            source=p.source,
            permissions=sorted(p.permissions),
            csrf_token=p.csrf_token,
        )


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class NewAccount(Credentials):
    display_name: str | None = Field(default=None, max_length=128)


class NewUser(NewAccount):
    groups: list[str] = Field(default_factory=list)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class UserPatch(BaseModel):
    groups: list[str] | None = None
    password: str | None = Field(default=None, min_length=1, max_length=1024)


class UserOut(BaseModel):
    id: int
    subject: str
    display_name: str
    source: Literal["local", "forward", "oidc"]
    groups: list[str]
    created_at: int
    last_seen: int | None

    @classmethod
    def of(cls, u: User) -> UserOut:
        return cls(
            id=u.id,
            subject=u.subject,
            display_name=u.display_name,
            source=u.source,
            groups=sorted(u.groups),
            created_at=u.created_at,
            last_seen=u.last_seen,
        )


class UserList(BaseModel):
    users: list[UserOut]


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ----------------------------------------------------------------------------- session


@router.get("/backends", response_model=Backends)
async def backends(request: Request) -> Backends:
    svc = deps.auth(request)
    return Backends(
        backends=list(svc.settings.backends),
        setup_required=await svc.setup_required(),
        registration="local" in svc.settings.backends and svc.settings.local.allow_registration,
    )


@router.get("/me", response_model=Me)
async def me(request: Request) -> Me:
    return Me.of(await deps.principal(request))


@router.post("/setup", response_model=Me, status_code=201)
async def setup(request: Request, body: NewAccount, response: Response) -> Me:
    svc = deps.auth(request)
    await svc.setup(body.username, body.password, body.display_name)
    principal, token = await svc.login(body.username, body.password, _client_ip(request))
    svc.set_cookie(response, request, token)
    return Me.of(principal)


@router.post("/register", response_model=Me, status_code=201)
async def register(request: Request, body: NewAccount, response: Response) -> Me:
    svc = deps.auth(request)
    await svc.register(body.username, body.password, body.display_name)
    principal, token = await svc.login(body.username, body.password, _client_ip(request))
    svc.set_cookie(response, request, token)
    return Me.of(principal)


@router.post("/login", response_model=Me)
async def login(request: Request, body: Credentials, response: Response) -> Me:
    svc = deps.auth(request)
    principal, token = await svc.login(body.username, body.password, _client_ip(request))
    svc.set_cookie(response, request, token)
    return Me.of(principal)


@router.post("/logout", status_code=204)
async def logout(request: Request) -> Response:
    svc = deps.auth(request)
    await deps.principal(request)
    await svc.logout(request)
    response = Response(status_code=204)
    svc.clear_cookie(response, request)
    return response


@router.post("/password", status_code=204)
async def change_password(request: Request, body: PasswordChange) -> Response:
    p = await deps.principal(request)
    await deps.auth(request).change_password(p, body.current_password, body.new_password)
    # set_password drops every session for the user, including this one.
    response = Response(status_code=204)
    deps.auth(request).clear_cookie(response, request)
    return response


# ----------------------------------------------------------------------------- oidc


def _redirect_uri(request: Request) -> str:
    configured = deps.auth(request).settings.oidc.redirect_url
    if configured:
        return configured
    url = request.url_for("oidc_callback")
    # Behind a TLS-terminating proxy the ASGI scheme is http; the IdP only accepts the
    # registered https redirect. Setting oidc.redirect_url is the explicit override.
    return str(url.replace(scheme="https") if is_https(request) else url)


def _safe_next(value: str | None) -> str:
    # Same-origin paths only: an absolute URL or protocol-relative "//host" is an open redirect.
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


@router.get("/oidc/start", name="oidc_start")
async def oidc_start(request: Request, next: str | None = None) -> RedirectResponse:
    url = await deps.auth(request).oidc.start(_redirect_uri(request))
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        NEXT_COOKIE,
        _safe_next(next),
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=is_https(request),
        path="/api/v1/auth/oidc",
    )
    return response


@router.get("/oidc/callback", name="oidc_callback")
async def oidc_callback(
    request: Request, state: str | None = None, code: str | None = None, error: str | None = None
) -> RedirectResponse:
    svc = deps.auth(request)
    if error:
        raise HTTPException(status_code=400, detail=f"IdP returned error {error!r}")
    ident = await svc.oidc.finish(_redirect_uri(request), state, code)
    user = await svc.mirror_external(ident)
    _, token = await svc.start_session(user, request)
    response = RedirectResponse(_safe_next(request.cookies.get(NEXT_COOKIE)), status_code=302)
    svc.set_cookie(response, request, token)
    response.delete_cookie(NEXT_COOKIE, path="/api/v1/auth/oidc")
    return response


# ----------------------------------------------------------------------------- users


@router.get("/users", response_model=UserList)
async def list_users(request: Request) -> UserList:
    await deps.require(request, USERS_MANAGE)
    users = await asyncio.to_thread(deps.auth(request).store.list_users)
    return UserList(users=[UserOut.of(u) for u in users])


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(request: Request, body: NewUser) -> UserOut:
    actor = await deps.require(request, USERS_MANAGE)
    user = await deps.auth(request).create_local_user(
        body.username,
        body.password,
        body.display_name,
        groups=set(body.groups),
        actor=actor.subject,
    )
    return UserOut.of(user)


@router.patch("/users/{user_id}", response_model=UserOut)
async def patch_user(request: Request, user_id: int, body: UserPatch) -> UserOut:
    actor = await deps.require(request, USERS_MANAGE)
    user = await deps.auth(request).update_user(
        user_id, groups=body.groups, password=body.password, actor=actor
    )
    return UserOut.of(user)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(request: Request, user_id: int) -> Response:
    actor = await deps.require(request, USERS_MANAGE)
    await deps.auth(request).delete_user(user_id, actor=actor)
    return Response(status_code=204)


def auth_error_response(exc: AuthError) -> Response:
    headers = {}
    retry = getattr(exc, "retry_after", None)
    if retry is not None:
        headers["Retry-After"] = str(retry)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status, headers=headers)


__all__ = ["ADMIN_GROUP", "auth_error_response", "router"]
