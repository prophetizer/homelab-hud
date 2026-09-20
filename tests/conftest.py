# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest

from hud.config import ConfigManager

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    d = tmp_path / "config"
    d.mkdir()
    return d


@pytest.fixture
def manager(config_dir: Path) -> ConfigManager:
    return ConfigManager(config_dir, poll_interval=0.05)


ADMIN = {"username": "admin", "password": "correct-horse-battery", "display_name": "Admin"}


def sign_in_admin(client: object) -> dict[str, str]:
    """First-run setup on a fresh app: creates the admin, keeps the session cookie on the
    client and returns the CSRF header every mutating call must carry."""
    from fastapi.testclient import TestClient  # noqa: PLC0415 — keep conftest import-light

    assert isinstance(client, TestClient)
    r = client.post("/api/v1/auth/setup", json=ADMIN)
    assert r.status_code == 201, r.text
    me = r.json()
    client.headers["X-CSRF-Token"] = me["csrf_token"]
    return me
