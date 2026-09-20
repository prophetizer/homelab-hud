# SPDX-License-Identifier: Apache-2.0
"""``PATCH /api/v1/boards/{name}`` — the layout editor's write path (PLAN.md §8.3)."""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from hud.main import create_app
from hud.store.tables import audit_log
from tests.conftest import sign_in_admin
from tests.test_api_health import _env

# Deliberately messy: a header comment, mixed flow/block grids, a trailing comment, an
# anchor nothing aliases, and a widget with explicit w/h. All of it must survive an edit
# untouched. (Flow mappings are written compact, `{a: 1}`; ruamel does not keep interior
# brace spacing, so the fixture is written the way write-back emits it.)
BOARD = """\
# Media board — hand-written, keep my comments
apiVersion: hud/v1
kind: Board
metadata:
  name: media
  title: Media   # shown in the sidebar
  visible_to: [household]
spec:
  layout:
    columns: &cols {sm: 1, md: 2, lg: 4}
  widgets:
    - id: hello
      type: static
      grid: {col: 1, row: 1}
      display: {text: "hi"}
    - id: wide
      type: static
      grid:
        col: 2   # second column
        row: 1
        w: 2
        h: 2
      display:
        text: "wide"
    - id: nogrid
      type: static
      grid: {col: 4, row: 1, w: 1, h: 1}
      display: {text: "explicit defaults"}
"""

RBAC = """\
apiVersion: hud/v1
kind: RBAC
spec:
  groups:
    admins: { permissions: ["*"] }
    household: { permissions: ["boards:view:media"] }
    editors: { permissions: ["boards:view:*", "boards:edit:media"] }
  defaults: { unmatched_group: guests }
"""


def _app(tmp_path: Path) -> tuple[TestClient, Path]:
    env = _env(tmp_path)
    (env.config_dir / "boards").mkdir(parents=True)
    (env.config_dir / "boards" / "media.yaml").write_text(BOARD)
    (env.config_dir / "rbac.yaml").write_text(RBAC)
    return TestClient(create_app(env)), env.config_dir / "boards" / "media.yaml"


def _patch(client: TestClient, revision: str, *widgets: dict[str, object]) -> object:
    return client.patch(
        "/api/v1/boards/media", json={"revision": revision, "widgets": list(widgets)}
    )


def test_patch_moves_widgets_and_preserves_the_rest(tmp_path: Path) -> None:
    client, path = _app(tmp_path)
    with client:
        sign_in_admin(client)
        before = client.get("/api/v1/boards/media").json()
        assert len(before["revision"]) == 12
        r = _patch(
            client,
            before["revision"],
            {"id": "hello", "grid": {"col": 3, "row": 2, "w": 1, "h": 1}},
            {"id": "wide", "grid": {"col": 1, "row": 3, "w": 3, "h": 1}},
        )
        assert r.status_code == 200, r.text
        after = r.json()
        assert after["revision"] != before["revision"]
        grids = {w["id"]: w["grid"] for w in after["widgets"]}
        assert grids["hello"] == {"col": 3, "row": 2, "w": 1, "h": 1}
        assert grids["wide"] == {"col": 1, "row": 3, "w": 3, "h": 1}
        assert grids["nogrid"] == {"col": 4, "row": 1, "w": 1, "h": 1}  # untouched
        # A follow-up GET agrees, and the config version moved (the watcher is not needed).
        assert client.get("/api/v1/boards/media").json()["revision"] == after["revision"]

    text = path.read_text()
    assert text.startswith("# Media board — hand-written, keep my comments\n")
    assert "  title: Media   # shown in the sidebar\n" in text
    assert "    columns: &cols {sm: 1, md: 2, lg: 4}\n" in text
    # Flow-style grid stays flow-style and minimal: w/h were default and absent, still absent.
    assert "      grid: {col: 3, row: 2}\n" in text
    # Block-style grid stays block-style, keeps its inline comment, only values change.
    block = [
        "      grid:",
        "        col: 1   # second column",
        "        row: 3",
        "        w: 3",
        "        h: 1",
    ]
    assert "\n".join(block) + "\n" in text
    assert "      grid: {col: 4, row: 1, w: 1, h: 1}\n" in text


def test_patch_revision_conflict(tmp_path: Path) -> None:
    client, path = _app(tmp_path)
    with client:
        sign_in_admin(client)
        loaded = client.get("/api/v1/boards/media").json()
        # A hand edit lands after the client loaded the board.
        path.write_text(path.read_text().replace("title: Media ", "title: Films "))
        r = _patch(client, loaded["revision"], {"id": "hello", "grid": {"col": 2, "row": 2}})
        assert r.status_code == 409, r.text
        assert "reload and retry" in r.json()["detail"]
        current = r.headers["X-Board-Revision"]
        assert current != loaded["revision"]
        # Nothing was written: the hand edit is intact and the widget did not move.
        assert "title: Films " in path.read_text()
        assert "grid: {col: 1, row: 1}" in path.read_text()
        # Retrying with the current revision succeeds.
        r = _patch(client, current, {"id": "hello", "grid": {"col": 2, "row": 2}})
        assert r.status_code == 200, r.text
        assert r.json()["title"] == "Films"


def test_patch_validation(tmp_path: Path) -> None:
    client, path = _app(tmp_path)
    with client:
        sign_in_admin(client)
        rev = client.get("/api/v1/boards/media").json()["revision"]
        r = _patch(client, rev, {"id": "ghost", "grid": {"col": 1, "row": 1}})
        assert r.status_code == 422 and "ghost" in r.json()["detail"]
        r = _patch(
            client,
            rev,
            {"id": "hello", "grid": {"col": 1, "row": 1}},
            {"id": "hello", "grid": {"col": 2, "row": 1}},
        )
        assert r.status_code == 422 and "twice" in r.json()["detail"]
        r = _patch(client, rev, {"id": "hello", "grid": {"col": 13, "row": 1}})
        assert r.status_code == 422  # Grid schema: col <= 12
        r = client.patch("/api/v1/boards/media", json={"revision": rev, "widgets": []})
        assert r.status_code == 422
        assert (
            client.patch(
                "/api/v1/boards/nope",
                json={"revision": rev, "widgets": [{"id": "x", "grid": {"col": 1, "row": 1}}]},
            ).status_code
            == 404
        )
        assert path.read_text() == BOARD, "no failed request may touch the file"


def test_patch_permissions_and_audit(tmp_path: Path) -> None:
    client, _ = _app(tmp_path)
    with client:
        sign_in_admin(client)
        for name, groups in (("viewer", ["household"]), ("editor", ["editors"])):
            r = client.post(
                "/api/v1/auth/users",
                json={"username": name, "password": "a-long-enough-password", "groups": groups},
            )
            assert r.status_code == 201, r.text

        def as_user(name: str) -> str:
            client.post("/api/v1/auth/logout")
            r = client.post(
                "/api/v1/auth/login", json={"username": name, "password": "a-long-enough-password"}
            )
            assert r.status_code == 200
            client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
            return str(client.get("/api/v1/boards/media").json()["revision"])

        rev = as_user("viewer")
        r = _patch(client, rev, {"id": "hello", "grid": {"col": 2, "row": 2}})
        assert r.status_code == 403 and "boards:edit:media" in r.json()["detail"]

        rev = as_user("editor")
        r = _patch(client, rev, {"id": "hello", "grid": {"col": 2, "row": 2}})
        assert r.status_code == 200, r.text
        # CSRF applies to PATCH like any other mutation.
        client.headers.pop("X-CSRF-Token")
        assert (
            _patch(
                client, r.json()["revision"], {"id": "hello", "grid": {"col": 1, "row": 1}}
            ).status_code
            == 403
        )

        with client.app.state.engine.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                select(audit_log).where(audit_log.c.action == "boards.layout")
            ).all()
        assert [(r.subject, r.target, r.result) for r in rows] == [("editor", "media", "ok")]
        assert '"hello"' in rows[0].detail_json
