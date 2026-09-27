# SPDX-License-Identifier: Apache-2.0
"""Weather — current conditions and a short forecast from Open-Meteo (no API key).

The only data that leaves the network is the location, and it is rounded to one decimal
place (about 10 km) before it is sent, whatever precision the config holds. One resource,
``{provider}:current:{location}``, with temperature, feels-like, humidity and
precipitation-chance metrics (Celsius and percent: units normalize at the boundary;
``units: imperial`` is a display hint carried in attrs, never a stored unit).

A failed fetch is this provider failing, shown with its error and last success
(invariant 6) — never yesterday's sunshine presented as today's.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from pydantic import Field, field_validator

from hud.providers.sdk import (
    Metric,
    PluginConfig,
    PluginProvider,
    PollResult,
    ProviderContext,
    ProviderPollError,
    Resource,
    Schedule,
    SourceUnit,
    State,
    make_uid,
    register,
)

KIND = "current"
_LOCATION = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")
PRECISION = 1  # decimal places sent: ~10 km

CURRENT = (
    "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,is_day,"
    "wind_speed_10m,precipitation"
)
DAILY = (
    "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
    "sunrise,sunset"
)

# WMO weather interpretation codes (Open-Meteo docs) → (condition, day icon, night icon).
_WMO: dict[int, tuple[str, str, str]] = {
    0: ("Clear", "mdi-weather-sunny", "mdi-weather-night"),
    1: ("Mainly clear", "mdi-weather-partly-cloudy", "mdi-weather-night-partly-cloudy"),
    2: ("Partly cloudy", "mdi-weather-partly-cloudy", "mdi-weather-night-partly-cloudy"),
    3: ("Overcast", "mdi-weather-cloudy", "mdi-weather-cloudy"),
    45: ("Fog", "mdi-weather-fog", "mdi-weather-fog"),
    48: ("Freezing fog", "mdi-weather-fog", "mdi-weather-fog"),
    51: ("Light drizzle", "mdi-weather-rainy", "mdi-weather-rainy"),
    53: ("Drizzle", "mdi-weather-rainy", "mdi-weather-rainy"),
    55: ("Heavy drizzle", "mdi-weather-rainy", "mdi-weather-rainy"),
    56: ("Freezing drizzle", "mdi-weather-snowy-rainy", "mdi-weather-snowy-rainy"),
    57: ("Freezing drizzle", "mdi-weather-snowy-rainy", "mdi-weather-snowy-rainy"),
    61: ("Light rain", "mdi-weather-rainy", "mdi-weather-rainy"),
    63: ("Rain", "mdi-weather-rainy", "mdi-weather-rainy"),
    65: ("Heavy rain", "mdi-weather-pouring", "mdi-weather-pouring"),
    66: ("Freezing rain", "mdi-weather-snowy-rainy", "mdi-weather-snowy-rainy"),
    67: ("Freezing rain", "mdi-weather-snowy-rainy", "mdi-weather-snowy-rainy"),
    71: ("Light snow", "mdi-weather-snowy", "mdi-weather-snowy"),
    73: ("Snow", "mdi-weather-snowy", "mdi-weather-snowy"),
    75: ("Heavy snow", "mdi-weather-snowy-heavy", "mdi-weather-snowy-heavy"),
    77: ("Snow grains", "mdi-weather-snowy", "mdi-weather-snowy"),
    80: ("Rain showers", "mdi-weather-rainy", "mdi-weather-rainy"),
    81: ("Rain showers", "mdi-weather-pouring", "mdi-weather-pouring"),
    82: ("Violent rain showers", "mdi-weather-pouring", "mdi-weather-pouring"),
    85: ("Snow showers", "mdi-weather-snowy", "mdi-weather-snowy"),
    86: ("Heavy snow showers", "mdi-weather-snowy-heavy", "mdi-weather-snowy-heavy"),
    95: ("Thunderstorm", "mdi-weather-lightning-rainy", "mdi-weather-lightning-rainy"),
    96: ("Thunderstorm, hail", "mdi-weather-hail", "mdi-weather-hail"),
    99: ("Thunderstorm, hail", "mdi-weather-hail", "mdi-weather-hail"),
}


def describe(code: object, is_day: bool = True) -> tuple[str, str]:
    """(condition, icon) for a WMO code; an unknown code is said so, not guessed."""
    if not isinstance(code, int) or code not in _WMO:
        return "Unknown", "mdi-weather-cloudy-alert"
    text, day, night = _WMO[code]
    return text, day if is_day else night


class WeatherConfig(PluginConfig):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    location: str = "home"  # the stable id: part of the uid
    title: str | None = None
    units: Literal["metric", "imperial"] = "metric"
    days: int = Field(default=3, ge=1, le=7)
    base_url: str = "https://api.open-meteo.com"

    @field_validator("location")
    @classmethod
    def _location(cls, v: str) -> str:
        if not _LOCATION.fullmatch(v):
            msg = "location must be [a-z0-9_-], starting with a letter or digit"
            raise ValueError(msg)
        return v

    @field_validator("base_url")
    @classmethod
    def _absolute(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            msg = "base_url must be an absolute http(s) URL"
            raise ValueError(msg)
        return v.rstrip("/")


@register("weather", config_model=WeatherConfig)
class Weather(PluginProvider):
    config: WeatherConfig

    def __init__(self, ctx: ProviderContext, config: WeatherConfig, schedule: Schedule) -> None:
        super().__init__(ctx, config, schedule)
        self._client: httpx.AsyncClient | None = None

    @property
    def query(self) -> dict[str, str]:
        """What is sent: the rounded location and the fields asked for, nothing else."""
        return {
            "latitude": f"{round(self.config.latitude, PRECISION):.{PRECISION}f}",
            "longitude": f"{round(self.config.longitude, PRECISION):.{PRECISION}f}",
            "current": CURRENT,
            "daily": DAILY,
            "forecast_days": str(self.config.days),
            "timezone": "auto",
        }

    async def startup(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self.config.base_url,
            timeout=httpx.Timeout(10.0),
            headers={"User-Agent": "HUD-weather/1"},
        )

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def discover(self) -> list[Resource]:
        return [self._resource(State.UNKNOWN, {}, datetime.now(UTC))]

    async def collect(self, resources: list[Resource]) -> PollResult:
        body = await self._get()
        now = datetime.now(UTC)
        current = body.get("current")
        if not isinstance(current, dict):
            raise ProviderPollError(self.name, "/v1/forecast: no current block in response")
        is_day = current.get("is_day") != 0
        condition, icon = describe(current.get("weather_code"), is_day)
        attrs: dict[str, object] = {
            "condition": condition,
            "icon": icon,
            "code": current.get("weather_code"),
            "is_day": is_day,
            "units": self.config.units,
            "temperature": current.get("temperature_2m"),
            "wind_kmh": current.get("wind_speed_10m"),
            "precipitation_mm": current.get("precipitation"),
            "observed_at": current.get("time"),
        }
        forecast = _forecast(body.get("daily"))
        if forecast:
            attrs["high"] = forecast[0]["high"]
            attrs["low"] = forecast[0]["low"]
            attrs["sunrise"] = forecast[0]["sunrise"]
            attrs["sunset"] = forecast[0]["sunset"]
            attrs["forecast"] = forecast
        res = self._resource(State.UP, attrs, now)
        readings = [
            ("temperature", current.get("temperature_2m"), SourceUnit.CELSIUS),
            ("feels_like", current.get("apparent_temperature"), SourceUnit.CELSIUS),
            ("humidity", current.get("relative_humidity_2m"), SourceUnit.PCT),
            (
                "precipitation_chance",
                forecast[0]["precipitation_chance"] if forecast else None,
                SourceUnit.PCT,
            ),
        ]
        metrics = [
            Metric.from_source(
                resource_uid=res.uid, name=name, value=float(v), source_unit=unit, ts=now
            )
            for name, v, unit in readings
            if isinstance(v, int | float) and not isinstance(v, bool)
        ]
        return PollResult(resources=[res], metrics=metrics)

    async def _get(self) -> dict[str, Any]:
        if self._client is None:
            raise ProviderPollError(self.name, "provider not started")
        try:
            resp = await self._client.get("/v1/forecast", params=self.query)
        except httpx.TimeoutException as exc:
            raise ProviderPollError(self.name, "GET /v1/forecast: timed out") from exc
        except httpx.HTTPError as exc:
            msg = f"GET /v1/forecast: {type(exc).__name__}"
            raise ProviderPollError(self.name, msg) from exc
        if resp.status_code >= 300:
            raise ProviderPollError(self.name, f"GET /v1/forecast: HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderPollError(self.name, "GET /v1/forecast: response is not JSON") from exc
        if not isinstance(body, dict):
            raise ProviderPollError(self.name, "GET /v1/forecast: response is not an object")
        return body

    def _resource(self, state: State, attrs: dict[str, object], now: datetime) -> Resource:
        return Resource(
            uid=make_uid(self.name, KIND, self.config.location),
            provider=self.name,
            kind=KIND,
            name=self.config.title or self.config.location.replace("-", " ").title(),
            state=state,
            attrs=attrs,
            fetched_at=now,
        )


def _forecast(daily: object) -> list[dict[str, Any]]:
    """Open-Meteo's daily block is parallel arrays; rows are easier to render."""
    if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
        return []

    def col(key: str, i: int) -> Any:  # noqa: ANN401
        v = daily.get(key)
        return v[i] if isinstance(v, list) and i < len(v) else None

    out = []
    for i, date in enumerate(daily["time"]):
        condition, icon = describe(col("weather_code", i))
        out.append(
            {
                "date": date,
                "condition": condition,
                "icon": icon,
                "high": col("temperature_2m_max", i),
                "low": col("temperature_2m_min", i),
                "precipitation_chance": col("precipitation_probability_max", i),
                "sunrise": col("sunrise", i),
                "sunset": col("sunset", i),
            }
        )
    return out


__all__ = ["Weather", "WeatherConfig", "describe"]
