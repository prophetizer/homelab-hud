# SPDX-License-Identifier: Apache-2.0
"""Resolves one :class:`Principal` per request from whichever backends are enabled, and owns
the login flows (PLAN.md §10.1).

Backend order in ``auth.backends`` is precedence: the first backend that identifies the
caller wins. ``local`` and ``oidc`` both arrive through the session cookie; a session is
honoured only while the backend that created it is still enabled, so disabling a backend
in YAML logs its users out on the next request.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
from typing import TYPE_CHECKING

from sqlalchemy import Engine
from starlette.requests import Request
from starlette.responses import Response

from hud.auth.backends.base import ExternalIdentity
from hud.auth.backends.forward import ForwardBackend
from hud.auth.backends.oidc import OidcBackend
from hud.auth.errors import (
    BackendDisabledError,
    InvalidCredentialsError,
    InvalidUsernameError,
    LockoutError,
    NoSuchUserError,
    RateLimitedError,
    RegistrationDisabledError,
    SetupNotRequiredError,
    UserConflictError,
    WeakPasswordError,
)
from hud.auth.limiter import LoginLimiter
from hud.auth.passwords import hash_password, needs_rehash, verify_password
from hud.auth.principal import Principal
from hud.auth.rbac import Authorizer
from hud.auth.store import AuthStore, User, UserExistsError
from hud.config.schemas import AuthSettings
from hud.config.schemas.duration import parse_duration

if TYPE_CHECKING:
    from hud.config import ConfigSnapshot, SecretResolver

log = logging.getLogger(__name__)

_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ADMIN_GROUP = "admins"


class AuthService:
    def __init__(
        self,
        engine: Engine,
        secrets_resolver: SecretResolver,
        settings: AuthSettings,
        authorizer: Authorizer,
    ) -> None:
        self.store = AuthStore(engine)
        self._secrets = secrets_resolver
        self.settings = settings
        self.authorizer = authorizer
        # Tight: 10 failures per address, and per account from one address — so guessing at
        # an account locks it only where the guesses come from. Loose: 50 per account from
        # anywhere, the backstop against a guesser spread over many addresses.
        self.limiter = LoginLimiter()
        self.account_limiter = LoginLimiter(max_failures=50)
        # Per-process key for CSRF tokens of identities that have no session row (forward).
        self._csrf_key = secrets.token_bytes(32)
        self._forward: ForwardBackend | None = None
        self._oidc: OidcBackend | None = None
        self._configure()

    # ------------------------------------------------------------------ configuration

    def _configure(self) -> None:
        s = self.settings
        self._forward = ForwardBackend(s.forward) if "forward" in s.backends else None
        self._oidc = OidcBackend(s.oidc, self._secrets) if "oidc" in s.backends else None

    async def apply(self, snapshot: ConfigSnapshot) -> None:
        """Config reload hook: pick up backend and RBAC changes without a restart."""
        self.settings = snapshot.settings.spec.auth
        self.authorizer = Authorizer(snapshot.rbac.spec)
        self._configure()

    @property
    def oidc(self) -> OidcBackend:
        if self._oidc is None:
            raise BackendDisabledError("oidc")
        return self._oidc

    @property
    def session_lifetime(self) -> int:
        return parse_duration(self.settings.session.lifetime)

    @property
    def cookie_name(self) -> str:
        return self.settings.session.cookie_name

    # ------------------------------------------------------------------ resolution

    async def resolve(self, request: Request) -> Principal | None:
        cached = getattr(request.state, "principal", None)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        principal: Principal | None = None
        for backend in self.settings.backends:
            if backend == "forward" and self._forward is not None:
                ident = self._forward.identify(request)
                if ident is not None:
                    principal = await self._principal_from_external(ident)
            elif backend in ("local", "oidc"):
                principal = await self._principal_from_session(request, backend)
            if principal is not None:
                break
        request.state.principal = principal
        return principal

    async def _principal_from_session(self, request: Request, source: str) -> Principal | None:
        token = request.cookies.get(self.cookie_name)
        if not token:
            return None
        found = await asyncio.to_thread(self.store.lookup_session, token)
        if found is None:
            return None
        session, user = found
        if user.source != source:
            return None
        return self._principal(user, csrf_token=session.csrf_token)

    async def _principal_from_external(self, ident: ExternalIdentity) -> Principal:
        user = await asyncio.to_thread(
            self.store.upsert_external,
            ident.subject,
            source=ident.source,
            display_name=ident.display_name,
            groups=set(ident.groups),
        )
        return self._principal(user, csrf_token=self._stateless_csrf(user))

    def _principal(self, user: User, *, csrf_token: str) -> Principal:
        return Principal(
            subject=user.subject,
            display_name=user.display_name,
            groups=user.groups,
            source=user.source,
            permissions=self.authorizer.permissions_for(user.groups),
            csrf_token=csrf_token,
            user_id=user.id,
        )

    def _stateless_csrf(self, user: User) -> str:
        msg = f"{user.source}:{user.subject}".encode()
        return hmac.new(self._csrf_key, msg, hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------ local flows

    def _require_local(self) -> None:
        if "local" not in self.settings.backends:
            raise BackendDisabledError("local")

    async def setup_required(self) -> bool:
        """True while ``local`` is enabled and no local account exists yet (§14.2)."""
        if "local" not in self.settings.backends:
            return False
        return await asyncio.to_thread(self.store.count_users, "local") == 0

    def _check_password(self, password: str) -> None:
        minimum = self.settings.local.min_password_length
        if len(password) < minimum:
            raise WeakPasswordError(minimum)

    @staticmethod
    def _check_username(username: str) -> str:
        username = username.strip().lower()
        if not _USERNAME.fullmatch(username):
            raise InvalidUsernameError()
        return username

    async def setup(self, username: str, password: str, display_name: str | None) -> User:
        self._require_local()
        if not await self.setup_required():
            raise SetupNotRequiredError()
        username = self._check_username(username)
        self._check_password(password)
        user = await asyncio.to_thread(
            self.store.create_user,
            username,
            source="local",
            display_name=display_name or username,
            password_hash=hash_password(password),
            groups={ADMIN_GROUP},
        )
        await asyncio.to_thread(self.store.audit, username, "auth.setup", username, "ok")
        log.info("first-run setup created local admin %r", username)
        return user

    async def register(self, username: str, password: str, display_name: str | None) -> User:
        self._require_local()
        if not self.settings.local.allow_registration:
            raise RegistrationDisabledError()
        return await self.create_local_user(
            username, password, display_name, groups=set(), actor=None
        )

    async def create_local_user(
        self,
        username: str,
        password: str,
        display_name: str | None,
        *,
        groups: set[str],
        actor: str | None,
    ) -> User:
        self._require_local()
        username = self._check_username(username)
        self._check_password(password)
        try:
            user = await asyncio.to_thread(
                self.store.create_user,
                username,
                source="local",
                display_name=display_name or username,
                password_hash=hash_password(password),
                groups=groups,
            )
        except UserExistsError as exc:
            raise UserConflictError(username) from exc
        await asyncio.to_thread(
            self.store.audit, actor, "users.create", username, "ok", {"groups": sorted(groups)}
        )
        return user

    async def login(self, username: str, password: str, client_ip: str) -> tuple[Principal, str]:
        """Returns the principal and the cookie value. Never says which of user/password
        was wrong, and runs a hash verification even for unknown users."""
        self._require_local()
        username = username.strip().lower()
        keys = (f"ip:{client_ip}", f"user:{username}@{client_ip}")
        account = f"user:{username}"
        wait = max(self.limiter.retry_after(*keys), self.account_limiter.retry_after(account))
        if wait:
            await asyncio.to_thread(
                self.store.audit, username, "auth.login", None, "rate_limited", {"ip": client_ip}
            )
            raise RateLimitedError(wait)
        user, stored_hash = await asyncio.to_thread(self.store.password_hash_for, username)
        ok = await asyncio.to_thread(verify_password, stored_hash, password)
        if not ok or user is None:
            self.limiter.record_failure(*keys)
            self.account_limiter.record_failure(account)
            await asyncio.to_thread(
                self.store.audit, username, "auth.login", None, "denied", {"ip": client_ip}
            )
            raise InvalidCredentialsError()
        self.limiter.reset(*keys)
        self.account_limiter.reset(account)
        if stored_hash is not None and needs_rehash(stored_hash):
            await asyncio.to_thread(self.store.set_password, user.id, hash_password(password))
        token, session = await asyncio.to_thread(
            self.store.create_session, user.id, self.session_lifetime
        )
        await asyncio.to_thread(
            self.store.audit, username, "auth.login", None, "ok", {"ip": client_ip}
        )
        return self._principal(user, csrf_token=session.csrf_token), token

    async def change_password(self, principal: Principal, current: str, new: str) -> None:
        self._require_local()
        if principal.source != "local" or principal.user_id is None:
            raise BackendDisabledError("local")
        _, stored_hash = await asyncio.to_thread(self.store.password_hash_for, principal.subject)
        if not await asyncio.to_thread(verify_password, stored_hash, current):
            raise InvalidCredentialsError()
        self._check_password(new)
        await asyncio.to_thread(self.store.set_password, principal.user_id, hash_password(new))
        await asyncio.to_thread(
            self.store.audit, principal.subject, "auth.password", principal.subject, "ok"
        )

    async def mirror_external(self, ident: ExternalIdentity) -> User:
        return await asyncio.to_thread(
            self.store.upsert_external,
            ident.subject,
            source=ident.source,
            display_name=ident.display_name,
            groups=set(ident.groups),
        )

    async def update_user(
        self,
        user_id: int,
        *,
        groups: set[str] | list[str] | None,
        password: str | None,
        actor: Principal,
    ) -> User:
        user = await asyncio.to_thread(self.store.get_user, user_id)
        if user is None:
            raise NoSuchUserError(user_id)
        changed: dict[str, object] = {}
        if groups is not None:
            new_groups = set(groups)
            demoting_self = user.id == actor.user_id and ADMIN_GROUP in user.groups
            if demoting_self and ADMIN_GROUP not in new_groups:
                raise LockoutError("you cannot remove yourself from the admins group")
            await asyncio.to_thread(self.store.set_groups, user_id, new_groups)
            changed["groups"] = sorted(new_groups)
        if password is not None:
            if user.source != "local":
                raise BackendDisabledError("local")
            self._check_password(password)
            await asyncio.to_thread(self.store.set_password, user_id, hash_password(password))
            changed["password"] = True
        await asyncio.to_thread(
            self.store.audit, actor.subject, "users.update", user.subject, "ok", changed
        )
        updated = await asyncio.to_thread(self.store.get_user, user_id)
        assert updated is not None
        return updated

    async def delete_user(self, user_id: int, *, actor: Principal) -> None:
        if user_id == actor.user_id:
            raise LockoutError("you cannot delete your own account")
        user = await asyncio.to_thread(self.store.get_user, user_id)
        if user is None:
            raise NoSuchUserError(user_id)
        await asyncio.to_thread(self.store.delete_user, user_id)
        await asyncio.to_thread(self.store.audit, actor.subject, "users.delete", user.subject, "ok")

    # ------------------------------------------------------------------ sessions

    async def start_session(self, user: User, request: Request) -> tuple[Principal, str]:
        """Session for an identity that arrived by a login flow other than password (OIDC)."""
        token, session = await asyncio.to_thread(
            self.store.create_session, user.id, self.session_lifetime
        )
        return self._principal(user, csrf_token=session.csrf_token), token

    async def logout(self, request: Request) -> None:
        token = request.cookies.get(self.cookie_name)
        if token:
            await asyncio.to_thread(self.store.delete_session, token)
        principal = getattr(request.state, "principal", None)
        if principal is not None:
            await asyncio.to_thread(self.store.audit, principal.subject, "auth.logout", None, "ok")

    def set_cookie(self, response: Response, request: Request, token: str) -> None:
        response.set_cookie(
            self.cookie_name,
            token,
            max_age=self.session_lifetime,
            httponly=True,
            samesite="lax",
            secure=is_https(request),
            path="/",
        )

    def clear_cookie(self, response: Response, request: Request) -> None:
        response.delete_cookie(
            self.cookie_name,
            httponly=True,
            samesite="lax",
            secure=is_https(request),
            path="/",
        )

    async def purge_expired(self) -> int:
        return await asyncio.to_thread(self.store.purge_expired_sessions)


def is_https(request: Request) -> bool:
    """True over TLS, or behind a proxy that says so. ``X-Forwarded-Proto`` is honoured from
    any peer because it can only *raise* the ``Secure`` flag: a forged ``https`` over plain
    HTTP yields a cookie the browser refuses to store, which harms only the forger."""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


__all__ = ["ADMIN_GROUP", "AuthService", "is_https"]
