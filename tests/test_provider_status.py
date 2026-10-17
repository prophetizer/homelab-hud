# SPDX-License-Identifier: Apache-2.0
"""The provider status snapshot and its command: secrets masked when written, addresses
hidden when shown, a stale or missing snapshot never reads as healthy, and a running HUD
writes one."""

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hud.config.redact import register
from hud.main import create_app
from hud.providers.base import ProviderHealth, ProviderStatus, ProviderTier
from hud.providers.status import SNAPSHOT_NAME, hide_addresses, main, report, snapshot
from tests.test_api_health import _env

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _health(name: str, status: ProviderStatus, error: str | None = None) -> ProviderHealth:
    return ProviderHealth(
        name=name,
        tier=ProviderTier.DECLARATIVE,
        status=status,
        last_success=NOW - timedelta(seconds=40),
        last_error=error,
        consecutive_failures=3 if error else 0,
        resource_count=5,
    )


def test_the_snapshot_masks_every_registered_secret() -> None:
    register("tok-very-secret-123")
    text = snapshot(
        [_health("immich", ProviderStatus.DEGRADED, "401 for tok-very-secret-123")], NOW
    )
    assert "tok-very-secret-123" not in text
    assert json.loads(text)["providers"][0]["name"] == "immich"


def test_the_report_hides_addresses_unless_asked() -> None:
    error = "ConnectError: http://scrutiny:8080/api/summary via 192.0.2.7:8080"
    data = json.loads(snapshot([_health("scrutiny", ProviderStatus.DEGRADED, error)], NOW))
    (hidden,) = report(data, NOW)
    assert "<url>" in hidden and "<ip>" in hidden and "scrutiny:8080" not in hidden
    assert "40s ago" in hidden and "3 failures in a row" in hidden
    (shown,) = report(data, NOW, show_addresses=True)
    assert "http://scrutiny:8080/api/summary" in shown
    assert hide_addresses("no address here") == "no address here"


def _write(tmp_path: Path, written: datetime, *providers: ProviderHealth) -> None:
    (tmp_path / SNAPSHOT_NAME).write_text(snapshot(providers, written))


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HUD_DATA_DIR", str(tmp_path))
    return tmp_path


def test_exit_codes_say_how_things_stand(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([]) == 2  # no snapshot yet
    now = datetime.now(UTC)
    _write(data_dir, now, _health("scrutiny", ProviderStatus.OK))
    assert main([]) == 0
    _write(data_dir, now, _health("a", ProviderStatus.OK), _health("b", ProviderStatus.ERROR))
    assert main([]) == 1
    assert main(["--name", "a"]) == 0
    assert main(["--name", "nope"]) == 3
    _write(data_dir, now - timedelta(minutes=5), _health("scrutiny", ProviderStatus.OK))
    assert main([]) == 2  # stale: HUD may be stopped
    assert "STALE" in capsys.readouterr().err


def test_json_output_hides_addresses_too(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(
        data_dir,
        datetime.now(UTC),
        _health("tdarr", ProviderStatus.DEGRADED, "timeout http://tdarr:8266/api/v2/status"),
    )
    capsys.readouterr()
    assert main(["--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["providers"][0]["last_error"] == "timeout <url>"


def test_a_running_hud_writes_the_snapshot(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(create_app(env)):
        path = env.data_dir / SNAPSHOT_NAME
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        data = json.loads(path.read_text())
    assert "written_at" in data and isinstance(data["providers"], list)
