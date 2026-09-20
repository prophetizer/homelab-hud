# SPDX-License-Identifier: Apache-2.0
"""OpenID Connect authorization-code + PKCE against any discovery document (PLAN.md §10.1).

Nothing here names an IdP. The one knob that differs between them is ``groups_claim``, a
dotted path into the ID token (falling back to userinfo) for the group list.

Pending flows (state → nonce, verifier) live in process memory for ten minutes: a single
dashboard process, no broker (Appendix B).

HTTP goes through our own ``httpx`` client rather than authlib's integration: that shim
picks ``httpx2`` whenever it is importable, which would make tests exercise a different
stack than production. authlib supplies only the pure parts — the PKCE challenge and the
ID-token claim validation.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import TYPE_CHECKING, Any, cast

import httpx
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from authlib.oidc.core import CodeIDToken
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError, JoseError
from joserfc.jwk import KeySet
from joserfc.jws import JWSRegistry

from hud.auth.backends.base import ExternalIdentity
from hud.auth.errors import OidcFlowError
from hud.config.schemas.settings import OidcSettings
from hud.config.secrets import secret_name

if TYPE_CHECKING:
    from hud.config import SecretResolver

log = logging.getLogger(__name__)

FLOW_TTL = 600
METADATA_TTL = 3600
HTTP_TIMEOUT = 10.0


class OidcBackend:
    def __init__(self, settings: OidcSettings, secrets_resolver: SecretResolver) -> None:
        self.settings = settings
        self._secrets = secrets_resolver
        self._metadata: dict[str, Any] | None = None
        self._metadata_at = 0.0
        self._jwks: dict[str, Any] | None = None
        self._pending: dict[str, tuple[float, str, str]] = {}  # state → (t, nonce, verifier)

    # ------------------------------------------------------------------ discovery

    async def metadata(self, force: bool = False) -> dict[str, Any]:
        if self._metadata is None or force or time.monotonic() - self._metadata_at > METADATA_TTL:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                try:
                    resp = await client.get(self.settings.discovery_url)
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    raise OidcFlowError(f"OIDC discovery failed: {exc}") from exc
            data = resp.json()
            for key in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
                if key not in data:
                    raise OidcFlowError(f"OIDC discovery document lacks {key!r}")
            self._metadata = data
            self._metadata_at = time.monotonic()
            self._jwks = None
        return self._metadata

    async def jwks(self, force: bool = False) -> KeySet:
        if self._jwks is None or force:
            meta = await self.metadata()
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                try:
                    resp = await client.get(meta["jwks_uri"])
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    raise OidcFlowError(f"OIDC JWKS fetch failed: {exc}") from exc
            self._jwks = resp.json()
        return KeySet.import_key_set(cast("Any", self._jwks))

    def _client_secret(self) -> str:
        return self._secrets.resolve(secret_name(self.settings.client_secret))

    # ------------------------------------------------------------------ flow

    def _sweep(self) -> None:
        cutoff = time.monotonic() - FLOW_TTL
        for state in [s for s, (t, _, _) in self._pending.items() if t < cutoff]:
            del self._pending[state]

    async def start(self, redirect_uri: str) -> str:
        """Returns the IdP URL to send the browser to."""
        self._sweep()
        meta = await self.metadata()
        state = secrets.token_urlsafe(16)
        nonce = secrets.token_urlsafe(16)
        verifier = secrets.token_urlsafe(48)
        params = {
            "response_type": "code",
            "client_id": self.settings.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.settings.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": create_s256_code_challenge(verifier),
            "code_challenge_method": "S256",
        }
        self._pending[state] = (time.monotonic(), nonce, verifier)
        return str(httpx.URL(meta["authorization_endpoint"]).copy_merge_params(params))

    async def finish(
        self, redirect_uri: str, state: str | None, code: str | None
    ) -> ExternalIdentity:
        self._sweep()
        if not state or state not in self._pending:
            raise OidcFlowError("unknown or expired OIDC state; start the login again")
        _, nonce, verifier = self._pending.pop(state)
        if not code:
            raise OidcFlowError("IdP returned no authorization code")
        meta = await self.metadata()
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            token = await self._exchange_code(client, meta, redirect_uri, code, verifier)
            claims = await self._validate_id_token(token, nonce, meta)
            groups = _dig(claims, self.settings.groups_claim)
            if groups is None and "userinfo_endpoint" in meta and "access_token" in token:
                info = await self._userinfo(client, meta, str(token["access_token"]))
                groups = _dig(info, self.settings.groups_claim)
                claims = {**info, **claims}
        return ExternalIdentity(
            subject=str(claims["sub"]),
            source="oidc",
            display_name=claims.get("name") or claims.get("preferred_username"),
            email=claims.get("email"),
            groups=frozenset(str(g) for g in groups) if isinstance(groups, list) else frozenset(),
        )

    async def _exchange_code(
        self,
        client: httpx.AsyncClient,
        meta: dict[str, Any],
        redirect_uri: str,
        code: str,
        verifier: str,
    ) -> dict[str, Any]:
        # client_secret_basic (RFC 6749 §2.3.1) — what every discovery-capable IdP accepts.
        try:
            resp = await client.post(
                meta["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                    "client_id": self.settings.client_id,
                },
                auth=(self.settings.client_id, self._client_secret()),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise OidcFlowError(f"token exchange failed: {exc}") from exc
        try:
            body = resp.json()
        except ValueError as exc:
            raise OidcFlowError(
                f"token endpoint returned non-JSON (HTTP {resp.status_code})"
            ) from exc
        if resp.status_code != 200 or not isinstance(body, dict):
            err = body.get("error", "?") if isinstance(body, dict) else "?"
            raise OidcFlowError(f"token exchange failed: HTTP {resp.status_code} {err}")
        return body

    @staticmethod
    async def _userinfo(
        client: httpx.AsyncClient, meta: dict[str, Any], access_token: str
    ) -> dict[str, Any]:
        """Best-effort: a missing userinfo only costs the groups claim, never the login."""
        try:
            resp = await client.get(
                meta["userinfo_endpoint"], headers={"Authorization": f"Bearer {access_token}"}
            )
            resp.raise_for_status()
            info = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("OIDC userinfo fetch failed: %s", exc)
            return {}
        return info if isinstance(info, dict) else {}

    async def _validate_id_token(
        self, token: dict[str, Any], nonce: str, meta: dict[str, Any]
    ) -> dict[str, Any]:
        raw = token.get("id_token")
        if not raw:
            raise OidcFlowError("token response has no id_token")
        registry = JWSRegistry(
            algorithms=meta.get("id_token_signing_alg_values_supported") or ["RS256"],
            strict_check_header=False,
        )
        try:
            try:
                decoded = jwt.decode(raw, key=await self.jwks(), registry=registry)
            except InvalidKeyIdError:
                decoded = jwt.decode(raw, key=await self.jwks(force=True), registry=registry)
        except JoseError as exc:
            raise OidcFlowError(f"id_token rejected: {exc}") from exc
        claims = CodeIDToken(
            decoded.claims,
            decoded.header,
            {"iss": {"values": [meta["issuer"]]}},
            {
                "nonce": nonce,
                "client_id": self.settings.client_id,
                "access_token": token.get("access_token"),
            },
        )
        try:
            claims.validate(leeway=120)
        except Exception as exc:
            raise OidcFlowError(f"id_token claims invalid: {exc}") from exc
        return dict(claims)


def _dig(node: Any, path: str) -> Any:  # noqa: ANN401
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


__all__ = ["OidcBackend"]
