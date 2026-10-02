# SPDX-License-Identifier: Apache-2.0
"""The account page and the way back in: how the caller is signed in, signing out on
every device, the groups an admin can give, and the server-side password reset — which
asks at the terminal, never takes the password as an argument, and ends every session."""

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hud.auth import reset as reset_cli
from hud.auth.store import AuthStore
from hud.main import create_app
from tests.conftest import ADMIN, sign_in_admin
from tests.test_api_health import _env

NEW = "a-new-long-password"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(_env(tmp_path))) as c:
        yield c


def _store(client: TestClient) -> AuthStore:
    return client.app.state.auth.store  # type: ignore[attr-defined, no-any-return]


def _login(client: TestClient, password: str = ADMIN["password"]) -> int:
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": password})
    if r.status_code == 200:
        client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r.status_code


def test_the_account_says_how_you_are_signed_in(client: TestClient) -> None:
    assert client.get("/api/v1/auth/account").status_code == 401
    sign_in_admin(client)
    me = client.get("/api/v1/auth/account").json()
    assert me["subject"] == "admin" and me["source"] == "local" and me["groups"] == ["admins"]
    assert me["can_change_password"] is True and me["min_password_length"] == 12
    assert me["sessions"] == 1
    assert 6 * 86_400 < me["session_expires"] - time.time() <= 7 * 86_400  # the 7d default


def test_signing_out_everywhere_ends_every_session(tmp_path: Path) -> None:
    app = create_app(_env(tmp_path))
    with TestClient(app) as phone, TestClient(app) as laptop:
        sign_in_admin(phone)
        assert _login(laptop) == 200
        assert phone.get("/api/v1/auth/account").json()["sessions"] == 2
        assert phone.post("/api/v1/auth/logout-all").status_code == 204
        assert phone.get("/api/v1/auth/me").status_code == 401
        assert laptop.get("/api/v1/auth/me").status_code == 401


def test_an_admin_sees_the_groups_rbac_defines(client: TestClient) -> None:
    sign_in_admin(client)
    body = client.get("/api/v1/auth/groups").json()
    groups = {g["name"]: g for g in body["groups"]}
    assert groups["admins"]["admin"] is True
    assert body["unmatched_group"] == "guests"
    r = client.post(
        "/api/v1/auth/users",
        json={"username": "kid", "password": "a-long-enough-password", "groups": ["household"]},
    )
    assert r.status_code == 201, r.text
    client.post("/api/v1/auth/logout")
    r = client.post(
        "/api/v1/auth/login", json={"username": "kid", "password": "a-long-enough-password"}
    )
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert client.get("/api/v1/auth/groups").status_code == 403
    account = client.get("/api/v1/auth/account").json()
    assert account["groups"] == ["household"] and account["can_change_password"] is True


def test_the_reset_command_sets_a_new_password_and_signs_everyone_out(
    client: TestClient,
) -> None:
    sign_in_admin(client)
    store = _store(client)
    answers = iter([NEW, NEW])
    done, message = reset_cli.reset(store, "Admin", 12, ask=lambda _prompt: next(answers))
    assert done, message
    assert NEW not in message
    assert client.get("/api/v1/auth/me").status_code == 401  # this session ended too
    assert _login(client) == 401
    assert _login(client, NEW) == 200


@pytest.mark.parametrize(
    ("username", "typed", "says"),
    [
        ("nobody", [], "local accounts: admin"),
        ("admin", ["short"], "at least 12"),
        ("admin", [NEW, NEW + "x"], "differ"),
    ],
)
def test_the_reset_command_refuses_and_changes_nothing(
    client: TestClient, username: str, typed: list[str], says: str
) -> None:
    sign_in_admin(client)
    answers = iter(typed)
    done, message = reset_cli.reset(_store(client), username, 12, ask=lambda _prompt: next(answers))
    assert not done and says in message
    assert client.get("/api/v1/auth/me").status_code == 200
    assert _login(client) == 200


def test_the_reset_command_needs_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert reset_cli.main(["admin"]) == 2


def test_me_and_sign_in_say_when_the_session_ends(client: TestClient) -> None:
    setup = sign_in_admin(client)
    me = client.get("/api/v1/auth/me").json()
    assert me["session_expires"] == client.get("/api/v1/auth/account").json()["session_expires"]
    assert abs(setup["session_expires"] - me["session_expires"]) <= 2
    client.post("/api/v1/auth/logout")
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": ADMIN["password"]})
    assert 6 * 86_400 < r.json()["session_expires"] - time.time() <= 7 * 86_400
