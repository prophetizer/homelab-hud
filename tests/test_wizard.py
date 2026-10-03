# SPDX-License-Identifier: Apache-2.0
"""Connect a service (PLAN §8.5, round 3): containers are matched to bundled templates and
told apart by whether HUD can reach them, a connection is tested without writing anything,
and connecting writes an ordinary provider file — the credential in the owner-only secret
store, never in the file — refused for a non-admin, a repeat, or an address a service never
lives at."""

import json
import socket
import stat
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from hud.collector import LiveCache
from hud.config.secret_store import store_secret
from hud.config.secrets import SecretResolver
from hud.main import create_app
from hud.wizard import discover, guard_url, image_word, load_catalog, match_image, render
from tests.conftest import sign_in_admin
from tests.test_api_health import _env
from tests.test_widgets_engine import res

KEY = "0123456789abcdef0123456789abcdef"  # the shape of an *arr API key; a test value
SONARR_FIXTURES = Path(__file__).parent.parent / "templates" / "providers" / "fixtures" / "sonarr"


@pytest.mark.parametrize(
    ("image", "template"),
    [
        ("lscr.io/linuxserver/sonarr:latest", "sonarr"),
        ("ghcr.io/hotio/radarr@sha256:abc", "radarr"),
        ("plexinc/pms-docker:1.41", "plex"),
        ("adguard/adguardhome", "adguard-home"),
        ("registry.local:5000/homeassistant/home-assistant:stable", "home-assistant"),
        ("library/postgres:16", None),
    ],
)
def test_images_match_their_template(image: str, template: str | None) -> None:
    found = match_image(image, load_catalog())
    assert (found.name if found else None) == template


def test_the_catalog_is_every_bundled_template() -> None:
    catalog = load_catalog()
    assert {"sonarr", "radarr", "plex", "glances", "sportarr"} <= set(catalog)
    assert catalog["sonarr"].port == 8989 and image_word("a/b/Sonarr:1") == "sonarr"


def test_render_fills_the_url_and_leaves_the_secret_a_reference() -> None:
    t = load_catalog()["sonarr"]
    text = render(t, {"SONARR_BASE_URL": "http://sonarr:8989/"})
    assert "base_url: http://sonarr:8989\n" in text and "${SONARR_BASE_URL}" not in text
    assert "${secret:sonarr_api_key}" in text
    assert text.startswith("# Connected with HUD's Connect a service wizard")
    for bad in ("", "ftp://sonarr", "http://user:pw@sonarr", "http://s\nx", "http://${secret:x}"):
        with pytest.raises(ValueError):
            render(t, {"SONARR_BASE_URL": bad})


async def test_the_guard_refuses_what_a_service_never_is(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(host: str, port: int, *_a: object, **_k: object) -> list[tuple]:
        ip = {"meta.test": "169.254.169.254", "lab.test": "172.31.0.20"}.get(host, "127.0.0.1")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    for url in ("http://meta.test", "http://metadata.google.internal", "http://localhost:8080"):
        with pytest.raises(ValueError):
            await guard_url(url, own_port=8080)
    await guard_url("http://lab.test:8989", own_port=8080)  # a homelab address is fine
    await guard_url("http://localhost:8989", own_port=8080)  # loopback, another port


def test_discovery_says_which_containers_hud_can_reach() -> None:
    cache = LiveCache()
    cache.apply(
        "docker",
        "list",
        [
            res("docker:container:hud", image="homelabhud/homelabhud:abc", networks=["proxy"]),
            res("docker:container:sonarr", image="lscr.io/linuxserver/sonarr", networks=["proxy"]),
            res(
                "docker:container:radarr",
                image="lscr.io/linuxserver/radarr",
                networks=["media"],
                ports=["7879->7878/tcp"],
            ),
            res("docker:container:plex", image="plexinc/pms-docker", networks=["proxy"]),
            res("docker:container:db", image="postgres:16", networks=["proxy"]),
        ],
        [],
    )
    found = discover(cache, load_catalog(), connected={"plex"})
    by = {f.container: f for f in found}
    assert set(by) == {"sonarr", "radarr", "plex"}
    assert by["sonarr"].reachable is True and by["sonarr"].suggested_url == "http://sonarr:8989"
    assert by["radarr"].reachable is False
    assert by["radarr"].suggested_url == "http://<this-server>:7879" and by["radarr"].note
    assert found[-1].container == "plex" and found[-1].connected  # connected ones last


def test_a_stored_secret_is_owner_only_and_keeps_the_rest(tmp_path: Path) -> None:
    path = tmp_path / "secrets.yaml"
    path.write_text("# mine\nplex_token: abc\n")
    store_secret(tmp_path, "sonarr_api_key", KEY)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text()
    assert text.startswith("# mine\nplex_token: abc\n") and f"sonarr_api_key: {KEY}" in text
    resolver = SecretResolver(tmp_path, secrets_dir=tmp_path / "none", env={})
    assert resolver.resolve("sonarr_api_key") == KEY
    assert resolver.source("sonarr_api_key") == "secrets.yaml"
    assert resolver.source("nope") is None
    with pytest.raises(ValueError):
        store_secret(tmp_path, "../x", KEY)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    def fake(host: str, port: int, *_a: object, **_k: object) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.31.0.20", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    env = _env(tmp_path)
    return TestClient(create_app(env))


SONARR = {
    "template": "sonarr",
    "values": {"SONARR_BASE_URL": "http://sonarr.test:8989"},
    "secrets": {"sonarr_api_key": KEY},
}


def test_connecting_sonarr_end_to_end(client: TestClient) -> None:
    status = json.loads((SONARR_FIXTURES / "service.json").read_text())
    with client, respx.mock(assert_all_mocked=False) as mock:
        route = mock.get("http://sonarr.test:8989/api/v3/system/status").mock(
            return_value=httpx.Response(200, json=status)
        )
        assert client.get("/api/v1/wizard/catalog").status_code == 401
        sign_in_admin(client)
        catalog = {t["name"]: t for t in client.get("/api/v1/wizard/catalog").json()["templates"]}
        assert catalog["sonarr"]["connected"] is False
        assert {f["name"]: f["set_in"] for f in catalog["sonarr"]["fields"]} == {
            "SONARR_BASE_URL": None,
            "sonarr_api_key": None,
        }
        tested = client.post("/api/v1/wizard/test", json=SONARR)
        assert tested.status_code == 200 and tested.json()["ok"] is True, tested.text
        assert route.calls.last.request.headers["x-api-key"] == KEY
        assert KEY not in tested.text  # never echoed back

        done = client.post("/api/v1/wizard/connect", json=SONARR)
        assert done.status_code == 201, done.text
        assert KEY not in done.text
        written = (client.app.state.env.config_dir / "providers" / "sonarr.yaml").read_text()  # type: ignore[attr-defined]
        assert KEY not in written and "${secret:sonarr_api_key}" in written
        assert "base_url: http://sonarr.test:8989" in written
        again = client.post("/api/v1/wizard/connect", json=SONARR)
        assert again.status_code == 409
        catalog = {t["name"]: t for t in client.get("/api/v1/wizard/catalog").json()["templates"]}
        assert catalog["sonarr"]["connected"] is True
        assert {f["name"]: f["set_in"] for f in catalog["sonarr"]["fields"]}[
            "sonarr_api_key"
        ] == "secrets.yaml"


def test_the_wizard_refuses_what_it_should(client: TestClient) -> None:
    with client:
        sign_in_admin(client)
        bad = {**SONARR, "values": {"SONARR_BASE_URL": "http://169.254.169.254"}}
        assert client.post("/api/v1/wizard/test", json=bad).status_code == 422
        assert (
            client.post("/api/v1/wizard/connect", json={**SONARR, "template": "nope"}).status_code
            == 404
        )
        no_key = {**SONARR, "secrets": {}}
        assert client.post("/api/v1/wizard/connect", json=no_key).status_code == 422
        r = client.post(
            "/api/v1/auth/users",
            json={
                "username": "viewer",
                "password": "a-long-enough-password",
                "groups": ["household"],
            },
        )
        assert r.status_code == 201, r.text
        client.post("/api/v1/auth/logout")
        r = client.post(
            "/api/v1/auth/login", json={"username": "viewer", "password": "a-long-enough-password"}
        )
        client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        assert client.get("/api/v1/wizard/catalog").status_code == 403
        assert client.post("/api/v1/wizard/connect", json=SONARR).status_code == 403
