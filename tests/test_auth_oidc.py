# SPDX-License-Identifier: Apache-2.0
"""OIDC code + PKCE flow against a mocked IdP: discovery, token exchange, JWKS, userinfo."""

import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey

from hud.main import create_app
from hud.settings import HudEnv
from tests.test_api_health import _env

ISSUER = "https://idp.test"
KEY = RSAKey.generate_key(2048, {"kid": "k1", "use": "sig", "alg": "RS256"})

SETTINGS = f"""\
apiVersion: hud/v1
kind: Settings
spec:
  auth:
    backends: [oidc, local]
    oidc:
      discovery_url: {ISSUER}/.well-known/openid-configuration
      client_id: hud
      client_secret: ${{secret:oidc_client_secret}}
      groups_claim: groups
"""

DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/authorize",
    "token_endpoint": f"{ISSUER}/token",
    "jwks_uri": f"{ISSUER}/jwks",
    "userinfo_endpoint": f"{ISSUER}/userinfo",
    "id_token_signing_alg_values_supported": ["RS256"],
}


def _id_token(nonce: str, **extra: object) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "u-123",
        "aud": "hud",
        "exp": now + 300,
        "iat": now,
        "nonce": nonce,
        "name": "Ursula",
        "email": "u@example.test",
        **extra,
    }
    return jwt.encode({"alg": "RS256", "kid": "k1"}, claims, KEY)


@pytest.fixture
def env(tmp_path: Path) -> HudEnv:
    e = _env(tmp_path)
    e.config_dir.mkdir()
    (e.config_dir / "settings.yaml").write_text(SETTINGS)
    (e.config_dir / "secrets.yaml").write_text("oidc_client_secret: shh-not-really\n")
    return e


@pytest.fixture
def idp() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=httpx.Response(200, json=DISCOVERY)
        )
        router.get(f"{ISSUER}/jwks").mock(
            return_value=httpx.Response(200, json={"keys": [KEY.as_dict(private=False)]})
        )
        yield router


def _start(client: TestClient) -> tuple[str, str, str]:
    r = client.get("/api/v1/auth/oidc/start", params={"next": "/b/media"}, follow_redirects=False)
    assert r.status_code == 302, r.text
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert r.headers["location"].startswith(f"{ISSUER}/authorize?")
    assert q["response_type"] == ["code"] and q["client_id"] == ["hud"]
    assert q["code_challenge_method"] == ["S256"] and "code_challenge" in q
    assert q["redirect_uri"] == ["http://testserver/api/v1/auth/oidc/callback"]
    assert q["scope"] == ["openid profile email groups"]
    return q["state"][0], q["nonce"][0], q["code_challenge"][0]


def test_full_login_flow(env: HudEnv, idp: respx.MockRouter) -> None:
    with TestClient(create_app(env)) as client:
        assert client.get("/api/v1/auth/backends").json()["backends"] == ["oidc", "local"]
        state, nonce, challenge = _start(client)

        def token(request: httpx.Request) -> httpx.Response:
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"] and form["code"] == ["abc"]
            assert create_s256_code_challenge(form["code_verifier"][0]) == challenge
            assert form["redirect_uri"] == ["http://testserver/api/v1/auth/oidc/callback"]
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(
                200,
                json={
                    "access_token": "at",
                    "token_type": "Bearer",
                    "id_token": _id_token(nonce, groups=["household"]),
                },
            )

        idp.post(f"{ISSUER}/token").mock(side_effect=token)
        r = client.get(
            "/api/v1/auth/oidc/callback",
            params={"state": state, "code": "abc"},
            follow_redirects=False,
        )
        assert r.status_code == 302 and r.headers["location"] == "/b/media"
        me = client.get("/api/v1/auth/me").json()
        assert me["subject"] == "u-123" and me["source"] == "oidc"
        assert me["display_name"] == "Ursula" and me["groups"] == ["household"]
        assert me["permissions"] == ["boards:view:*"]  # default rbac.yaml
        # State is single-use.
        r = client.get(
            "/api/v1/auth/oidc/callback",
            params={"state": state, "code": "abc"},
            follow_redirects=False,
        )
        assert r.status_code == 400 and "state" in r.json()["detail"]


def test_groups_fall_back_to_userinfo(env: HudEnv, idp: respx.MockRouter) -> None:
    with TestClient(create_app(env)) as client:
        state, nonce, _ = _start(client)
        idp.post(f"{ISSUER}/token").mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "at", "token_type": "Bearer", "id_token": _id_token(nonce)},
            )
        )
        idp.get(f"{ISSUER}/userinfo").mock(
            return_value=httpx.Response(200, json={"sub": "u-123", "groups": ["admins"]})
        )
        r = client.get("/api/v1/auth/oidc/callback", params={"state": state, "code": "x"})
        assert r.status_code == 200  # followed the redirect to the SPA index
        assert client.get("/api/v1/auth/me").json()["permissions"] == ["*"]


def test_bad_tokens_are_rejected(env: HudEnv, idp: respx.MockRouter) -> None:
    with TestClient(create_app(env)) as client:
        for label, bad in (
            ("nonce", lambda n: _id_token("other-nonce")),
            ("aud", lambda n: _id_token(n, aud="someone-else")),
            ("iss", lambda n: _id_token(n, iss="https://evil.test")),
            ("exp", lambda n: _id_token(n, exp=int(time.time()) - 1000)),
            (
                "signature",
                lambda n: jwt.encode(
                    {"alg": "RS256", "kid": "k1"},
                    {"iss": ISSUER, "sub": "x", "aud": "hud", "exp": 9e9, "iat": 1, "nonce": n},
                    RSAKey.generate_key(2048, {"kid": "k1"}),
                ),
            ),
        ):
            state, nonce, _ = _start(client)
            idp.post(f"{ISSUER}/token").mock(
                return_value=httpx.Response(
                    200, json={"access_token": "at", "token_type": "Bearer", "id_token": bad(nonce)}
                )
            )
            r = client.get(
                "/api/v1/auth/oidc/callback",
                params={"state": state, "code": "x"},
                follow_redirects=False,
            )
            assert r.status_code == 400, label
            assert "id_token" in r.json()["detail"], label
            assert client.get("/api/v1/auth/me").status_code == 401, label


def test_idp_error_and_missing_config(env: HudEnv, idp: respx.MockRouter) -> None:
    with TestClient(create_app(env)) as client:
        r = client.get("/api/v1/auth/oidc/callback", params={"error": "access_denied"})
        assert r.status_code == 400
        idp.get(f"{ISSUER}/.well-known/openid-configuration").mock(return_value=httpx.Response(503))
        client.app.state.auth.oidc._metadata = None  # type: ignore[attr-defined]
        r = client.get("/api/v1/auth/oidc/start", follow_redirects=False)
        assert r.status_code == 400 and "discovery failed" in r.json()["detail"]


def test_open_redirect_is_neutralised(env: HudEnv, idp: respx.MockRouter) -> None:
    with TestClient(create_app(env)) as client:
        for nxt in ("https://evil.test/", "//evil.test", "javascript:alert(1)"):
            r = client.get("/api/v1/auth/oidc/start", params={"next": nxt}, follow_redirects=False)
            assert (r.cookies.get("hud_oidc_next") or "").strip('"') == "/", nxt
