# SPDX-License-Identifier: Apache-2.0
"""Every response says it came from HUD (X-HUD), so the web app can tell HUD's own 401
from a login gate's in front of it — Authelia's redirect or refusal — and reload into the
gate's sign-in instead of showing HUD's."""

from pathlib import Path

from fastapi.testclient import TestClient

from hud.main import create_app
from tests.test_api_health import _env


def test_every_kind_of_response_is_marked(tmp_path: Path) -> None:
    with TestClient(create_app(_env(tmp_path))) as client:
        responses = [
            client.get("/api/v1/health"),  # 200
            client.get("/api/v1/auth/me"),  # HUD's own 401
            client.get("/api/v1/nope"),  # 404
            client.post("/api/v1/auth/login", json={}),  # 422
        ]
        for r in responses:
            assert r.headers.get("x-hud") == "1", (r.request.url, r.status_code)
        assert {r.status_code for r in responses} == {200, 401, 404, 422}
