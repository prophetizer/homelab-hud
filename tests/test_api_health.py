# SPDX-License-Identifier: Apache-2.0
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hud import __version__
from hud.config import ConfigError
from hud.main import create_app
from hud.settings import HudEnv


def _env(tmp_path: Path, static: bool = True) -> HudEnv:
    static_dir = tmp_path / "dist"
    if static:
        (static_dir / "assets").mkdir(parents=True)
        (static_dir / "index.html").write_text("<!doctype html><title>HUD</title><div id=root>")
        (static_dir / "assets" / "app-abc123.js").write_text("console.log(1)")
        (static_dir / "favicon.svg").write_text("<svg/>")
    return HudEnv(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        static_dir=static_dir,
        config_poll_interval=60,
    )


def test_empty_config_boots_and_health_reports(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(create_app(env)) as client:
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["app_version"] == __version__
        assert len(body["config"]["version"]) == 12
        assert body["config"]["error"] is None
        assert body["db"]["size_bytes"] > 0
        assert body["db"]["revisions"] == {
            "dashboard": "0001_baseline",
            "metrics": "0001_baseline",
        }
        assert body["uptime_seconds"] >= 0
    assert (env.config_dir / "settings.yaml").exists(), "bootstrap must write settings.yaml"
    assert (env.data_dir / "dashboard.db").exists()
    assert (env.data_dir / "metrics.db").exists()


def test_restart_preserves_state_and_config_version(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(create_app(env)) as client:
        first = client.get("/api/v1/health").json()
    (env.config_dir / "settings.yaml").write_text(
        (env.config_dir / "settings.yaml").read_text().replace("theme: dark", "theme: light")
    )
    with TestClient(create_app(env)) as client:
        second = client.get("/api/v1/health").json()
    assert second["config"]["version"] != first["config"]["version"]
    assert second["db"]["revisions"] == first["db"]["revisions"]
    assert not (env.data_dir / "backups").exists(), "no migration ran, so no backup"


def test_yaml_error_refuses_startup_naming_file_and_line(tmp_path: Path) -> None:
    env = _env(tmp_path)
    env.config_dir.mkdir()
    bad = env.config_dir / "settings.yaml"
    bad.write_text(
        "apiVersion: hud/v1\nkind: Settings\nspec:\n  theme: dark\n   title: bad indent\n"
    )
    with pytest.raises(ConfigError) as ei, TestClient(create_app(env)):
        pass
    text = str(ei.value)
    assert str(bad) in text
    assert f"{bad}:5" in text


def test_spa_served_with_no_store_and_api_404_is_json(tmp_path: Path) -> None:
    with TestClient(create_app(_env(tmp_path))) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store"
        assert "<div id=root>" in r.text
        # client-side routes fall back to index.html
        assert client.get("/boards/media").text == r.text
        # real files at root are served as-is
        assert client.get("/favicon.svg").text == "<svg/>"
        # hashed assets served from /assets
        assert client.get("/assets/app-abc123.js").status_code == 200
        # unknown API paths never fall back to the SPA
        r = client.get("/api/v1/nope")
        assert r.status_code == 404
        assert r.json() == {"detail": "not found"}
        # path traversal cannot escape the static dir
        assert (
            client.get("/../pyproject.toml").text == r.text
            or "<div id=root>" in client.get("/../pyproject.toml").text
        )


def test_unbuilt_frontend_degrades_loudly(tmp_path: Path) -> None:
    with TestClient(create_app(_env(tmp_path, static=False))) as client:
        r = client.get("/")
        assert r.status_code == 503
        assert "not built" in r.text
        assert client.get("/api/v1/health").status_code == 200


def test_health_degraded_after_bad_reload(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(create_app(env)) as client:
        (env.config_dir / "settings.yaml").write_text(
            "kind: Settings\napiVersion: hud/v1\nspec: 3\n"
        )
        asyncio.run(client.app.state.config.reload())  # type: ignore[attr-defined]
        body = client.get("/api/v1/health").json()
        assert body["status"] == "degraded"
        assert "settings.yaml:3" in body["config"]["error"]
        assert len(body["config"]["version"]) == 12, "last good version still served"


def test_health_lists_providers_and_build_failures(tmp_path: Path) -> None:
    env = _env(tmp_path)
    providers = env.config_dir / "providers"
    providers.mkdir(parents=True)
    (providers / "broken.yaml").write_text(
        "apiVersion: hud/v1\nkind: Provider\nmetadata: {name: broken}\nspec:\n"
        "  transport: {base_url: '${NOT_SET_ANYWHERE}'}\n"
        "  resources:\n"
        "    - name: x\n      request: {path: /x}\n"
        "      map: {uid: 'broken:x:{{ item.n }}', kind: x, name: '{{ item.n }}'}\n"
    )
    with TestClient(create_app(env)) as client:
        body = client.get("/api/v1/health").json()
        assert body["status"] == "ok"  # HUD itself is fine; the provider is not
        (p,) = body["providers"]
        assert p["name"] == "broken" and p["status"] == "error"
        assert "NOT_SET_ANYWHERE" in p["last_error"]
