# SPDX-License-Identifier: Apache-2.0
"""The header: weather that says where it came from, search that HUD never sees, and a
backdrop that only ever comes from /config/backgrounds/."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hud.api.header import MAX_BACKGROUND_BYTES
from hud.config import ConfigError, ConfigManager, SecretResolver
from hud.config.schemas import ProviderDocument
from hud.config.schemas.settings import Appearance, HeaderSettings
from hud.main import create_app
from hud.models import Metric, Resource, State, Unit
from hud.providers import ProviderContext, ProviderPollError
from hud.providers.builtin.weather import Weather, describe
from hud.providers.factory import ProviderFactory
from hud.providers.sdk.loader import PluginLoader
from tests.conftest import sign_in_admin
from tests.test_api_health import _env
from tests.test_widgets_engine import T0

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

WEATHER_DOC = """\
apiVersion: hud/v1
kind: Provider
metadata: { name: weather }
spec:
  plugin: weather
  defaults: { interval: 15m }
  config:
    latitude: 41.87811
    longitude: -87.62980
    units: imperial
    days: 2
"""

FORECAST = {
    "current": {
        "time": "2026-09-27T14:00",
        "temperature_2m": 21.4,
        "apparent_temperature": 20.9,
        "relative_humidity_2m": 58,
        "weather_code": 2,
        "is_day": 1,
        "wind_speed_10m": 12.3,
        "precipitation": 0.0,
    },
    "daily": {
        "time": ["2026-09-27", "2026-09-28"],
        "weather_code": [2, 63],
        "temperature_2m_max": [23.1, 19.0],
        "temperature_2m_min": [14.2, 12.8],
        "precipitation_probability_max": [10, 80],
        "sunrise": ["2026-09-27T06:44", "2026-09-28T06:45"],
        "sunset": ["2026-09-27T18:38", "2026-09-28T18:36"],
    },
}


def build_weather(config_dir: Path, text: str = WEATHER_DOC) -> Weather:
    (config_dir / "settings.yaml").write_text("apiVersion: hud/v1\nkind: Settings\n")
    (config_dir / "providers").mkdir(exist_ok=True)
    (config_dir / "providers" / "weather.yaml").write_text(text)
    snap = ConfigManager(config_dir).load()
    if snap.quarantined:
        raise ConfigError(snap.quarantined[0].issues)
    (doc,) = [d.model for d in snap.documents if isinstance(d.model, ProviderDocument)]
    secrets = SecretResolver(config_dir, secrets_dir=config_dir / "none", env={})
    loader = PluginLoader()
    loader.add(Weather)
    p = ProviderFactory(loader)(doc, ProviderContext.create("weather", secrets, {}))
    assert isinstance(p, Weather)
    return p


async def test_weather_sends_only_a_rounded_location(config_dir: Path) -> None:
    p = build_weather(config_dir)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get("https://api.open-meteo.com/v1/forecast").mock(
            return_value=httpx.Response(200, json=FORECAST)
        )
        await p.startup()
        try:
            result = await p.poll("collect")
        finally:
            await p.shutdown()
    params = route.calls.last.request.url.params
    assert (params["latitude"], params["longitude"]) == ("41.9", "-87.6")
    assert set(params) == {"latitude", "longitude", "current", "daily", "forecast_days", "timezone"}
    (res,) = result.resources
    assert res.uid == "weather:current:home" and res.state is State.UP
    assert res.attrs["condition"] == "Partly cloudy"
    assert res.attrs["icon"] == "mdi-weather-partly-cloudy"
    assert res.attrs["units"] == "imperial"  # a display hint; values stay Celsius
    assert (res.attrs["high"], res.attrs["low"]) == (23.1, 14.2)
    assert [d["condition"] for d in res.attrs["forecast"]] == ["Partly cloudy", "Rain"]  # type: ignore[index, union-attr]
    readings = {m.name: (m.value, m.unit) for m in result.metrics}
    assert readings == {
        "temperature": (21.4, Unit.CELSIUS),
        "feels_like": (20.9, Unit.CELSIUS),
        "humidity": (58.0, Unit.PCT),
        "precipitation_chance": (10.0, Unit.PCT),
    }


async def test_a_failed_fetch_fails_the_provider(config_dir: Path) -> None:
    p = build_weather(config_dir)
    with respx.mock() as mock:
        mock.get("https://api.open-meteo.com/v1/forecast").mock(return_value=httpx.Response(503))
        await p.startup()
        try:
            with pytest.raises(ProviderPollError, match="HTTP 503"):
                await p.poll("collect")
        finally:
            await p.shutdown()


def test_describe_knows_night_and_admits_unknown_codes() -> None:
    assert describe(0, is_day=False) == ("Clear", "mdi-weather-night")
    assert describe(1234) == ("Unknown", "mdi-weather-cloudy-alert")
    assert describe(None)[0] == "Unknown"


@pytest.mark.parametrize("search", ["google", "none", "https://search.lab.example/?q={q}"])
def test_search_accepts_presets_none_and_https_templates(search: str) -> None:
    assert HeaderSettings(search=search).search == search


@pytest.mark.parametrize("search", ["altavista", "http://search.lab.example/?q={q}", "https://x"])
def test_search_refuses_anything_else(search: str) -> None:
    with pytest.raises(ValidationError):
        HeaderSettings(search=search)


@pytest.mark.parametrize(
    "name", ["../secrets.yaml", "/etc/passwd.png", "sub/wall.jpg", "wall.svg", ".hidden.png"]
)
def test_background_is_a_bare_image_file_name(name: str) -> None:
    with pytest.raises(ValidationError):
        Appearance(background=name)


SETTINGS = """\
apiVersion: hud/v1
kind: Settings
spec:
  timezone: America/Chicago
  header:
    search: kagi
    weather: weather:current:home
    stats:
      - { resource: glances:host:main, metric: cpu_percent, label: CPU }
  appearance:
    background: wall.png
    dim: 70
"""


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    env = _env(tmp_path)
    env.config_dir.mkdir(parents=True)
    (env.config_dir / "settings.yaml").write_text(SETTINGS)
    (env.config_dir / "backgrounds").mkdir()
    (env.config_dir / "backgrounds" / "wall.png").write_bytes(PNG)
    app = create_app(env)
    with TestClient(app) as c:
        cache = app.state.cache
        cache.register_provider("glances", {})
        host = Resource(
            uid="glances:host:main",
            provider="glances",
            kind="host",
            name="host",
            state=State.UP,
            fetched_at=T0,
        )
        cpu = Metric(
            resource_uid="glances:host:main", name="cpu_percent", value=37.5, unit=Unit.PCT, ts=T0
        )
        cache.apply("glances", "collect", [host], [cpu])
        yield c


def test_header_needs_a_session(client: TestClient) -> None:
    assert client.get("/api/v1/header").status_code == 401
    assert client.get("/api/v1/header/background").status_code == 401


def test_header_resolves_readings_and_says_when_weather_is_missing(client: TestClient) -> None:
    sign_in_admin(client)
    body = client.get("/api/v1/header").json()
    assert body["timezone"] == "America/Chicago"
    assert body["search_url"] == "https://kagi.com/search?q={q}"
    assert body["stats"] == [{"label": "CPU", "value": 37.5, "unit": "pct", "state": "up"}]
    # No weather provider is running: the header says so rather than showing nothing.
    assert body["weather"]["state"] == "unknown"
    assert "weather:current:home" in body["weather"]["error"]
    assert body["appearance"]["dim"] == 70
    assert body["appearance"]["background"].startswith("/api/v1/header/background?v=")


def test_header_weather_carries_the_reading(client: TestClient) -> None:
    app = client.app
    cache = app.state.cache  # type: ignore[attr-defined]
    cache.register_provider("weather", {})
    res = Resource(
        uid="weather:current:home",
        provider="weather",
        kind="current",
        name="Home",
        state=State.UP,
        attrs={"condition": "Rain", "icon": "mdi-weather-rainy", "temperature": 12.0, "secret": 1},
        fetched_at=T0,
    )
    hum = Metric(resource_uid=res.uid, name="humidity", value=91.0, unit=Unit.PCT, ts=T0)
    cache.apply("weather", "collect", [res], [hum])
    sign_in_admin(client)
    w = client.get("/api/v1/header").json()["weather"]
    assert w["attrs"] == {"condition": "Rain", "icon": "mdi-weather-rainy", "temperature": 12.0}
    assert w["metrics"] == {"humidity": 91.0}
    assert w["state"] == "up" and w["error"] is None


def test_background_is_served_from_its_folder_and_must_be_an_image(
    client: TestClient, tmp_path: Path
) -> None:
    sign_in_admin(client)
    r = client.get("/api/v1/header/background")
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert "sandbox" in r.headers["content-security-policy"]
    wall = tmp_path / "config" / "backgrounds" / "wall.png"
    wall.write_bytes(b"<html>not an image</html>")
    assert client.get("/api/v1/header/background").status_code == 404
    wall.write_bytes(PNG + b"\x00" * MAX_BACKGROUND_BYTES)
    assert client.get("/api/v1/header/background").status_code == 404
    wall.unlink()
    assert client.get("/api/v1/header/background").status_code == 404
    assert client.get("/api/v1/header").json()["appearance"]["background"] is None
