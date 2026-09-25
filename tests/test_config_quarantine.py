# SPDX-License-Identifier: Apache-2.0
"""Per-file quarantine through the real app (invariant 6: a broken provider degrades one
tile, never the dashboard).

The first live deployment hit the old behaviour: one malformed docker.yaml made the whole
reload fail, so plex and home-assistant never loaded either — and after any restart HUD
would have refused to start at all.
"""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from hud.main import create_app
from hud.settings import HudEnv
from tests.conftest import sign_in_admin
from tests.test_api_health import _env
from tests.test_api_providers import DEMO, THINGS

# The exact mistake from the deployment: ${...} unquoted inside a flow mapping.
BROKEN_DOCKER = """\
apiVersion: hud/v1
kind: Provider
metadata: { name: docker }
spec:
  plugin: docker
  config: { base_url: ${DOCKER_HOST} }
"""

BOARD = """\
apiVersion: hud/v1
kind: Board
metadata: {name: media, title: Media}
spec:
  widgets:
    - {id: note, type: static, grid: {col: 1, row: 1}, display: {text: hi}}
"""


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get("http://demo.lab/things").mock(return_value=httpx.Response(200, json=THINGS))
        yield router


def _providers(client: TestClient) -> dict[str, dict[str, object]]:
    return {p["name"]: p for p in client.get("/api/v1/providers").json()["providers"]}


def _setup(tmp_path: Path, files: dict[str, str]) -> HudEnv:
    env = _env(tmp_path)
    for rel, text in files.items():
        path = env.config_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return env


def test_broken_provider_at_startup_degrades_one_tile(
    tmp_path: Path, mock: respx.MockRouter
) -> None:
    env = _setup(tmp_path, {"providers/demo.yaml": DEMO, "providers/docker.yaml": BROKEN_DOCKER})
    with TestClient(create_app(env)) as client:  # starts; before this change it refused
        sign_in_admin(client)
        providers = _providers(client)
        # The good provider next to it loaded and polls normally.
        assert client.post("/api/v1/providers/demo/reload").json()["polled"] == {"things": True}
        assert providers["demo"]["status"] != "error"
        # The broken one is a failed tile that says why and where — not an absence.
        broken = providers["docker"]
        assert broken["status"] == "error"
        assert "config file is invalid, not loaded" in broken["last_error"]
        assert "docker.yaml:6:24" in broken["last_error"]
        assert "must be quoted" in broken["last_error"]  # the remedy travels with it
        # /health says so, and still answers 200 — the container stays healthy.
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        (q,) = body["config"]["quarantined"]
        # The file does not parse, so its metadata.name is unreadable: the kind is inferred
        # from the folder and the tile above is named after the file.
        assert q["kind"] == "Provider" and q["name"] is None
        assert q["file"] == "providers/docker.yaml"
        assert q["serving_last_good"] is False and q["issues"]


def test_bad_edit_keeps_serving_the_last_valid_version(
    tmp_path: Path, mock: respx.MockRouter
) -> None:
    env = _setup(tmp_path, {"providers/demo.yaml": DEMO})
    demo = env.config_dir / "providers" / "demo.yaml"
    with TestClient(create_app(env)) as client:
        sign_in_admin(client)
        config = client.app.state.config  # type: ignore[attr-defined]
        assert client.post("/api/v1/providers/demo/reload").status_code == 200

        # A typo lands in a working provider.
        demo.write_text(DEMO.replace("kind: Provider", "kind: Provider\nspec: [oops"))
        assert asyncio.run(config.reload()) is True
        # It keeps running on its last valid version, and the error is visible.
        assert _providers(client)["demo"]["status"] != "error"
        body = client.get("/api/v1/health").json()
        assert body["status"] == "degraded" and body["config"]["error"] is None
        (q,) = body["config"]["quarantined"]
        assert q["file"] == "providers/demo.yaml" and q["serving_last_good"] is True

        # Fixing the file clears it.
        demo.write_text(DEMO)
        assert asyncio.run(config.reload()) is True
        body = client.get("/api/v1/health").json()
        assert body["status"] == "ok" and body["config"]["quarantined"] == []


def test_broken_settings_still_refuses_to_start(tmp_path: Path) -> None:
    # Settings and RBAC stay all-or-nothing: running on a half-read auth config is worse
    # than not running.
    from hud.config import ConfigError  # noqa: PLC0415

    env = _setup(tmp_path, {"settings.yaml": "apiVersion: hud/v1\nkind: Settings\nspec: 3\n"})
    with pytest.raises(ConfigError), TestClient(create_app(env)):
        pass


def test_broken_board_is_not_listed_to_users(tmp_path: Path, mock: respx.MockRouter) -> None:
    """Its visible_to cannot be read, so listing it would risk disclosing it to someone not
    allowed to see it. Operators see it in /health; users simply do not get it."""
    broken = BOARD.replace("name: media", "name: broken").replace("widgets:", "widgets: [oops")
    env = _setup(tmp_path, {"boards/media.yaml": BOARD, "boards/broken.yaml": broken})
    with TestClient(create_app(env)) as client:
        sign_in_admin(client)
        assert [b["name"] for b in client.get("/api/v1/boards").json()["boards"]] == ["media"]
        assert client.get("/api/v1/boards/broken").status_code == 404
        (q,) = client.get("/api/v1/health").json()["config"]["quarantined"]
        assert q["kind"] == "Board" and q["file"] == "boards/broken.yaml"


def test_layout_editor_cannot_overwrite_a_quarantined_board(
    tmp_path: Path, mock: respx.MockRouter
) -> None:
    """A board serving its last valid version has a broken file on disk. A save from the
    editor must be refused, not silently replace the user's half-finished hand edit."""
    env = _setup(tmp_path, {"boards/media.yaml": BOARD})
    board = env.config_dir / "boards" / "media.yaml"
    with TestClient(create_app(env)) as client:
        sign_in_admin(client)
        revision = client.get("/api/v1/boards/media").json()["revision"]
        broken = BOARD.replace("widgets:", "widgets: [oops")
        board.write_text(broken)
        assert asyncio.run(client.app.state.config.reload()) is True  # type: ignore[attr-defined]
        # Still served from its last valid version, at the old revision.
        assert client.get("/api/v1/boards/media").json()["revision"] == revision
        r = client.patch(
            "/api/v1/boards/media",
            json={"revision": revision, "widgets": [{"id": "note", "grid": {"col": 2, "row": 1}}]},
        )
        assert r.status_code == 409, r.text
        assert board.read_text() == broken, "the broken file must be left exactly as written"
