# SPDX-License-Identifier: Apache-2.0
"""Auth flows and data-level enforcement through the real app (PLAN.md §10)."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from hud.main import create_app
from hud.settings import HudEnv
from tests.conftest import ADMIN, sign_in_admin
from tests.test_api_health import _env
from tests.test_api_providers import DEMO, THINGS

MEDIA_BOARD = """\
apiVersion: hud/v1
kind: Board
metadata: { name: media, title: Media }
spec:
  widgets:
    - id: alpha
      type: resource
      grid: { col: 1, row: 1 }
      source: { resource: "demo:thing:alpha" }
"""

INFRA_BOARD = """\
apiVersion: hud/v1
kind: Board
metadata: { name: infra, title: Infra, visible_to: [ops] }
spec:
  widgets:
    - id: all
      type: list
      grid: { col: 1, row: 1 }
      source: { select: { provider: demo } }
"""

RBAC = """\
apiVersion: hud/v1
kind: RBAC
spec:
  groups:
    admins: { permissions: ["*"] }
    household: { permissions: ["boards:view:media"] }
    guests: { permissions: [] }
  defaults: { unmatched_group: guests }
"""


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get("http://demo.lab/things").mock(return_value=httpx.Response(200, json=THINGS))
        yield router


def _write_config(env: HudEnv, settings_extra: str = "") -> None:
    (env.config_dir / "providers").mkdir(parents=True)
    (env.config_dir / "boards").mkdir()
    (env.config_dir / "providers" / "demo.yaml").write_text(DEMO)
    (env.config_dir / "boards" / "media.yaml").write_text(MEDIA_BOARD)
    (env.config_dir / "boards" / "infra.yaml").write_text(INFRA_BOARD)
    (env.config_dir / "rbac.yaml").write_text(RBAC)
    if settings_extra:
        (env.config_dir / "settings.yaml").write_text(
            "apiVersion: hud/v1\nkind: Settings\nspec:\n" + settings_extra
        )


@pytest.fixture
def app_env(tmp_path: Path, mock: respx.MockRouter) -> HudEnv:
    env = _env(tmp_path)
    _write_config(env)
    return env


def _login(client: TestClient, username: str, password: str) -> httpx.Response:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    if r.status_code == 200:
        client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r


# ----------------------------------------------------------------------------- first run


def test_first_run_setup_then_login(app_env: HudEnv) -> None:
    with TestClient(create_app(app_env)) as client:
        b = client.get("/api/v1/auth/backends").json()
        assert b == {"backends": ["local"], "setup_required": True, "registration": False}
        assert client.get("/api/v1/auth/me").status_code == 401
        assert client.get("/api/v1/boards").status_code == 401

        weak = client.post("/api/v1/auth/setup", json={**ADMIN, "password": "short"})
        assert weak.status_code == 422 and "12 characters" in weak.json()["detail"]

        me = sign_in_admin(client)
        assert me["subject"] == "admin" and me["groups"] == ["admins"]
        assert me["permissions"] == ["*"] and me["source"] == "local"
        assert client.get("/api/v1/auth/backends").json()["setup_required"] is False
        # Setup is one-shot.
        assert client.post("/api/v1/auth/setup", json=ADMIN).status_code == 409

        # Logout drops the session; the cookie is gone and /me is anonymous again.
        assert client.post("/api/v1/auth/logout").status_code == 204
        assert client.get("/api/v1/auth/me").status_code == 401

        assert _login(client, "admin", "wrong-password-here").status_code == 401
        assert _login(client, "nobody", ADMIN["password"]).status_code == 401
        assert _login(client, "ADMIN", ADMIN["password"]).status_code == 200  # case-folded
        assert client.get("/api/v1/auth/me").json()["subject"] == "admin"


def test_csrf_required_on_mutating_routes(app_env: HudEnv) -> None:
    with TestClient(create_app(app_env)) as client:
        sign_in_admin(client)
        good = client.headers.pop("X-CSRF-Token")
        r = client.post("/api/v1/providers/demo/reload")
        assert r.status_code == 403 and "X-CSRF-Token" in r.json()["detail"]
        client.headers["X-CSRF-Token"] = "nope"
        assert client.post("/api/v1/providers/demo/reload").status_code == 403
        client.headers["X-CSRF-Token"] = good
        assert client.post("/api/v1/providers/demo/reload").status_code == 200
        # GETs never need it.
        client.headers.pop("X-CSRF-Token")
        assert client.get("/api/v1/boards").status_code == 200


def test_login_rate_limit(app_env: HudEnv) -> None:
    with TestClient(create_app(app_env)) as client:
        sign_in_admin(client)
        client.post("/api/v1/auth/logout")
        svc = client.app.state.auth  # type: ignore[attr-defined]
        svc.limiter.max_failures = 3
        for _ in range(3):
            assert _login(client, "admin", "bad-password-attempt").status_code == 401
        r = _login(client, "admin", ADMIN["password"])  # even the right password now
        assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
        svc.limiter.reset("ip:testclient", "user:admin@testclient")
        assert _login(client, "admin", ADMIN["password"]).status_code == 200


def test_guesses_lock_an_account_only_where_they_come_from(app_env: HudEnv) -> None:
    """Behind a proxy (auth.trusted_proxies) each client is its own address: guesses at the
    admin from one address lock the admin out there, not from the LAN; an address spoofed in
    a header by a client that is not a trusted proxy is never believed."""
    (app_env.config_dir / "settings.yaml").write_text(
        "apiVersion: hud/v1\nkind: Settings\nspec:\n  auth:\n    trusted_proxies: [127.0.0.0/8]\n"
    )
    with TestClient(create_app(app_env), client=("127.0.0.1", 50000)) as client:
        sign_in_admin(client)
        client.post("/api/v1/auth/logout")
        svc = client.app.state.auth  # type: ignore[attr-defined]
        svc.limiter.max_failures = 3
        work = {"X-Forwarded-For": "203.0.113.9"}
        for _ in range(3):
            r = client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "wrong-guess-1"},
                headers=work,
            )
            assert r.status_code == 401
        blocked = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": ADMIN["password"]},
            headers=work,
        )
        assert blocked.status_code == 429
        lan = {"X-Forwarded-For": "172.31.1.1"}
        ok = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": ADMIN["password"]},
            headers=lan,
        )
        assert ok.status_code == 200  # the admin is not locked out from home


def test_session_survives_restart_and_expires(app_env: HudEnv) -> None:
    with TestClient(create_app(app_env)) as client:
        sign_in_admin(client)
        cookies = dict(client.cookies)
    with TestClient(create_app(app_env), cookies=cookies) as client:
        assert client.get("/api/v1/auth/me").status_code == 200
        # Expire every session and make sure the cookie is now worthless.
        from sqlalchemy import update  # noqa: PLC0415

        from hud.store.tables import sessions  # noqa: PLC0415

        with client.app.state.engine.begin() as conn:  # type: ignore[attr-defined]
            conn.execute(update(sessions).values(expires_at=1))
        assert client.get("/api/v1/auth/me").status_code == 401


# ----------------------------------------------------------------------------- rbac


@pytest.fixture
def two_users(app_env: HudEnv) -> Iterator[TestClient]:
    """Admin signed in; a ``household`` user and an ``ops`` user created by the admin."""
    with TestClient(create_app(app_env)) as client:
        sign_in_admin(client)
        assert client.post("/api/v1/providers/demo/reload").status_code == 200
        for name, groups in (("house", ["household"]), ("oper", ["ops"])):
            r = client.post(
                "/api/v1/auth/users",
                json={"username": name, "password": "a-long-enough-password", "groups": groups},
            )
            assert r.status_code == 201, r.text
        yield client


def test_boards_are_filtered_at_the_data_layer(two_users: TestClient) -> None:
    client = two_users
    assert [b["name"] for b in client.get("/api/v1/boards").json()["boards"]] == ["infra", "media"]

    client.post("/api/v1/auth/logout")
    assert _login(client, "house", "a-long-enough-password").status_code == 200
    assert [b["name"] for b in client.get("/api/v1/boards").json()["boards"]] == ["media"]
    assert client.get("/api/v1/boards/media").status_code == 200
    assert client.get("/api/v1/boards/infra").status_code == 404, "hidden, not forbidden"
    # Resources: only what the media board shows (alpha), never beta.
    uids = [r["uid"] for r in client.get("/api/v1/resources").json()["resources"]]
    assert uids == ["demo:thing:alpha"]
    assert client.get("/api/v1/resources/demo:thing:alpha").status_code == 200
    assert client.get("/api/v1/resources/demo:thing:beta").status_code == 404
    # No providers:view → no provider health, and /health degrades to liveness.
    assert client.get("/api/v1/providers").status_code == 403
    assert client.post("/api/v1/providers/demo/reload").status_code == 403
    assert set(client.get("/api/v1/health").json()) == {"status", "app_version"}
    assert client.get("/api/v1/auth/users").status_code == 403

    client.post("/api/v1/auth/logout")
    # ``ops`` holds no permission at all, but infra's visible_to names the group.
    assert _login(client, "oper", "a-long-enough-password").status_code == 200
    assert client.get("/api/v1/auth/me").json()["permissions"] == []
    assert [b["name"] for b in client.get("/api/v1/boards").json()["boards"]] == ["infra"]
    uids = [r["uid"] for r in client.get("/api/v1/resources").json()["resources"]]
    assert uids == ["demo:thing:alpha", "demo:thing:beta"]  # the list widget selects both


def test_rbac_reload_applies_without_restart(two_users: TestClient) -> None:
    import asyncio  # noqa: PLC0415

    client = two_users
    client.post("/api/v1/auth/logout")
    _login(client, "house", "a-long-enough-password")
    assert client.get("/api/v1/providers").status_code == 403
    cfg = client.app.state.config  # type: ignore[attr-defined]
    (cfg.config_dir / "rbac.yaml").write_text(
        RBAC.replace(
            'household: { permissions: ["boards:view:media"] }',
            'household: { permissions: ["boards:view:media", "providers:view"] }',
        )
    )
    assert asyncio.run(cfg.reload()) is True
    assert client.get("/api/v1/providers").status_code == 200


def test_user_management_guards(two_users: TestClient) -> None:
    client = two_users
    users = {u["subject"]: u for u in client.get("/api/v1/auth/users").json()["users"]}
    assert set(users) == {"admin", "house", "oper"}
    admin_id, house_id = users["admin"]["id"], users["house"]["id"]

    assert (
        client.post(
            "/api/v1/auth/users", json={"username": "house", "password": "x" * 12}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/auth/users", json={"username": "Bad Name", "password": "x" * 12}
        ).status_code
        == 422
    )
    # Lockout guards: cannot demote or delete yourself.
    r = client.patch(f"/api/v1/auth/users/{admin_id}", json={"groups": ["household"]})
    assert r.status_code == 409
    assert client.delete(f"/api/v1/auth/users/{admin_id}").status_code == 409
    # Promote house, then set a new password for them, then remove them.
    r = client.patch(
        f"/api/v1/auth/users/{house_id}", json={"groups": ["admins"], "password": "b" * 12}
    )
    assert r.status_code == 200 and r.json()["groups"] == ["admins"]
    assert client.delete(f"/api/v1/auth/users/{house_id}").status_code == 204
    assert client.delete(f"/api/v1/auth/users/{house_id}").status_code == 404

    # Self-service password change ends the session.
    r = client.post(
        "/api/v1/auth/password",
        json={"current_password": ADMIN["password"], "new_password": "c" * 12},
    )
    assert r.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    assert _login(client, "admin", "c" * 12).status_code == 200


def test_registration_toggle(tmp_path: Path, mock: respx.MockRouter) -> None:
    env = _env(tmp_path)
    _write_config(env, "  auth:\n    local: { allow_registration: true }\n")
    with TestClient(create_app(env)) as client:
        assert client.get("/api/v1/auth/backends").json()["registration"] is True
        r = client.post("/api/v1/auth/register", json={"username": "joe", "password": "p" * 12})
        assert r.status_code == 201
        assert r.json()["groups"] == [] and r.json()["permissions"] == []
        # The admin account is still unclaimed: registration never grants admin.
        assert client.get("/api/v1/auth/backends").json()["setup_required"] is False
    env2 = _env(tmp_path / "other")
    _write_config(env2)
    with TestClient(create_app(env2)) as client:
        assert (
            client.post(
                "/api/v1/auth/register", json={"username": "joe", "password": "p" * 12}
            ).status_code
            == 403
        )


# ----------------------------------------------------------------------------- forward


def test_forward_auth_fails_closed(tmp_path: Path, mock: respx.MockRouter) -> None:
    env = _env(tmp_path)
    _write_config(
        env,
        "  auth:\n    backends: [forward, local]\n"
        "    forward: { trusted_proxies: ['10.0.0.0/8'] }\n",
    )
    headers = {"Remote-User": "alice", "Remote-Groups": "household, ops", "Remote-Name": "Alice"}
    app = create_app(env)
    # Same headers from an untrusted peer: anonymous.
    with TestClient(app, client=("172.31.1.9", 1)) as client:
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    # Trusted peer, no user header: anonymous, not a crash.
    with TestClient(app, client=("10.1.2.3", 1)) as client:
        assert client.get("/api/v1/auth/me").status_code == 401
        me = client.get("/api/v1/auth/me", headers=headers).json()
        assert me["subject"] == "alice" and me["source"] == "forward"
        assert me["display_name"] == "Alice" and me["groups"] == ["household", "ops"]
        assert me["permissions"] == ["boards:view:media"]
        boards = client.get("/api/v1/boards", headers=headers).json()["boards"]
        assert [b["name"] for b in boards] == ["infra", "media"]  # ops via visible_to
        # Mutations still need the CSRF token, which /me handed out.
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 403
        r = client.post(
            "/api/v1/auth/logout", headers={**headers, "X-CSRF-Token": me["csrf_token"]}
        )
        assert r.status_code == 204
        # Groups are re-read on every request: the IdP is the source of truth.
        me = client.get("/api/v1/auth/me", headers={**headers, "Remote-Groups": "guests"}).json()
        assert me["permissions"] == []


def test_forward_requires_trusted_proxies(tmp_path: Path) -> None:
    env = _env(tmp_path)
    env.config_dir.mkdir()
    (env.config_dir / "settings.yaml").write_text(
        "apiVersion: hud/v1\nkind: Settings\nspec:\n  auth:\n    backends: [forward]\n"
    )
    from hud.config import ConfigError  # noqa: PLC0415

    with pytest.raises(ConfigError) as ei, TestClient(create_app(env)):
        pass
    assert "trusted_proxies is empty" in str(ei.value)


def test_empty_backends_refuse_to_start(tmp_path: Path) -> None:
    env = _env(tmp_path)
    env.config_dir.mkdir()
    (env.config_dir / "settings.yaml").write_text(
        "apiVersion: hud/v1\nkind: Settings\nspec:\n  auth:\n    backends: []\n"
    )
    from hud.config import ConfigError  # noqa: PLC0415

    with pytest.raises(ConfigError) as ei, TestClient(create_app(env)):
        pass
    assert "refuses to run without authentication" in str(ei.value)


def test_secure_cookie_behind_tls_proxy(app_env: HudEnv) -> None:
    with TestClient(create_app(app_env)) as client:
        r = client.post("/api/v1/auth/setup", json=ADMIN)
        assert "secure" not in r.headers["set-cookie"].lower()
        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": r.json()["csrf_token"]})
        r = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": ADMIN["password"]},
            headers={"X-Forwarded-Proto": "https"},
        )
        assert r.status_code == 200
        assert "secure" in r.headers["set-cookie"].lower()
