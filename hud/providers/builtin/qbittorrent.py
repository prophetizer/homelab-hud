# SPDX-License-Identifier: Apache-2.0
"""qBittorrent — transfer speeds and torrents, for a downloads widget (§12, media extras).

Resources:
- ``{provider}:service:main`` — version and connection status (connected is up,
  firewalled degraded, disconnected down), with ``speed_bps`` / ``upload_bps`` metrics;
- ``{provider}:download:{infohash}`` — the newest torrents (``filter``, default
  ``downloading``), each with ``progress_pct``, ``size_bytes`` and ``speed_bps``. The
  infohash *is* the torrent: it never changes, so history does not fork (invariant 8).

Sign-in: with ``username`` set, HUD logs in (``POST /api/v2/auth/login``, the one POST it
makes — authentication only, nothing is changed) and keeps the session cookie, signing in
again once if it expires. Without it, qBittorrent must allow HUD's address through its
"bypass authentication" whitelist. Otherwise read-only: every other call is a GET.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from pydantic import Field, SecretStr, field_validator, model_validator

from hud.config.schemas.duration import parse_duration
from hud.providers.base import HttpOptions
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

SERVICE_ID = "main"

# qBittorrent torrent states (WebUI API v2) → canonical state. Unlisted is unknown.
_TORRENT_STATE: dict[str, State] = {
    **dict.fromkeys(
        (
            "downloading",
            "metaDL",
            "forcedMetaDL",
            "forcedDL",
            "allocating",
            "checkingDL",
            "queuedDL",
            "checkingResumeData",
            "moving",
            "uploading",
            "forcedUP",
            "stalledUP",
            "queuedUP",
            "checkingUP",
        ),
        State.UP,
    ),
    "stalledDL": State.DEGRADED,  # no peers: worth a look
    **dict.fromkeys(("pausedDL", "stoppedDL", "pausedUP", "stoppedUP"), State.PAUSED),
    **dict.fromkeys(("error", "missingFiles"), State.DOWN),
}
_CONNECTION_STATE = {
    "connected": State.UP,
    "firewalled": State.DEGRADED,
    "disconnected": State.DOWN,
}


class QbittorrentConfig(PluginConfig):
    base_url: str
    username: str | None = None
    password: SecretStr | None = None
    title: str = "qBittorrent"
    filter: Literal["all", "downloading", "active", "seeding", "completed", "paused"] = (
        "downloading"
    )
    limit: int = Field(default=30, ge=1, le=200)
    timeout: str = "10s"
    verify_tls: bool = True

    @field_validator("base_url")
    @classmethod
    def _absolute(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            msg = "base_url must be an absolute http(s) URL"
            raise ValueError(msg)
        return v.rstrip("/")

    @field_validator("timeout")
    @classmethod
    def _dur(cls, v: str) -> str:
        parse_duration(v)
        return v

    @model_validator(mode="after")
    def _pair(self) -> QbittorrentConfig:
        if (self.username is None) != (self.password is None):
            msg = "set both username and password, or neither (whitelist bypass)"
            raise ValueError(msg)
        return self


def torrent_state(state: object) -> State:
    return _TORRENT_STATE.get(state, State.UNKNOWN) if isinstance(state, str) else State.UNKNOWN


@register("qbittorrent", config_model=QbittorrentConfig)
class Qbittorrent(PluginProvider):
    config: QbittorrentConfig

    def __init__(self, ctx: ProviderContext, config: QbittorrentConfig, schedule: Schedule) -> None:
        super().__init__(ctx, config, schedule)
        self._client: httpx.AsyncClient | None = None
        self._version: str | None = None
        self._signed_in = False

    @property
    def service_uid(self) -> str:
        return make_uid(self.name, "service", SERVICE_ID)

    async def startup(self) -> None:
        # qBittorrent refuses API calls whose Referer/Origin is not its own host (CSRF).
        self._client = self.ctx.new_http_client(
            HttpOptions(
                base_url=self.config.base_url,
                timeout=float(parse_duration(self.config.timeout)),
                verify_tls=self.config.verify_tls,
                headers={"Referer": self.config.base_url, "Origin": self.config.base_url},
            )
        )

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._signed_in = False

    async def discover(self) -> list[Resource]:
        version = await self._request("/api/v2/app/version", json_body=False)
        self._version = str(version).strip() or None
        return [self._service({}, datetime.now(UTC))]

    async def collect(self, resources: list[Resource]) -> PollResult:
        now = datetime.now(UTC)
        info = await self._request("/api/v2/transfer/info")
        torrents = await self._request(
            "/api/v2/torrents/info",
            filter=self.config.filter,
            sort="added_on",
            reverse="true",
            limit=str(self.config.limit),
        )
        if not isinstance(torrents, list):
            raise ProviderPollError(self.name, "/api/v2/torrents/info: not a list")
        info = info if isinstance(info, dict) else {}
        service = self._service(info, now)
        out = PollResult(resources=[service])
        for name, key in (("speed_bps", "dl_info_speed"), ("upload_bps", "up_info_speed")):
            if isinstance(info.get(key), int | float):
                out.metrics.append(
                    _metric(service.uid, name, info[key], SourceUnit.BYTES_PER_SEC, now)
                )
        for t in torrents:
            if not isinstance(t, dict) or not isinstance(t.get("hash"), str) or not t["hash"]:
                continue
            res = self._torrent(t, now)
            out.resources.append(res)
            for name, key, unit in (
                ("progress_pct", "progress", SourceUnit.RATIO),
                ("size_bytes", "size", SourceUnit.BYTES),
                ("speed_bps", "dlspeed", SourceUnit.BYTES_PER_SEC),
            ):
                v = t.get(key)
                if isinstance(v, int | float) and not isinstance(v, bool):
                    out.metrics.append(_metric(res.uid, name, v, unit, now))
        out.metrics.append(
            _metric(service.uid, "torrents", len(out.resources) - 1, SourceUnit.COUNT, now)
        )
        return out

    # ---------------------------------------------------------------- mapping

    def _service(self, info: dict[str, Any], now: datetime) -> Resource:
        connection = info.get("connection_status")
        return Resource(
            uid=self.service_uid,
            provider=self.name,
            kind="service",
            name=self.config.title,
            state=_CONNECTION_STATE.get(connection, State.UNKNOWN)
            if isinstance(connection, str)
            else State.UNKNOWN,
            attrs={"version": self._version, "connection": connection},
            links={"ui": self.config.base_url},
            fetched_at=now,
        )

    def _torrent(self, t: dict[str, Any], now: datetime) -> Resource:
        eta = t.get("eta")
        return Resource(
            uid=make_uid(self.name, "download", t["hash"].lower()),
            provider=self.name,
            kind="download",
            name=str(t.get("name") or t["hash"]),
            state=torrent_state(t.get("state")),
            parent_uid=self.service_uid,
            attrs={
                "status": t.get("state"),
                "category": t.get("category") or None,
                # 8640000 is qBittorrent's "infinite".
                "eta_seconds": eta if isinstance(eta, int) and 0 <= eta < 8_640_000 else None,
                "added_at": t.get("added_on"),
                "ratio": t.get("ratio"),
            },
            fetched_at=now,
        )

    # ---------------------------------------------------------------- transport

    async def _login(self) -> None:
        assert self._client is not None
        if self.config.username is None or self.config.password is None:
            return
        try:
            resp = await self._client.post(
                "/api/v2/auth/login",
                data={
                    "username": self.config.username,
                    "password": self.config.password.get_secret_value(),
                },
            )
        except httpx.HTTPError as exc:
            msg = f"POST /api/v2/auth/login: {type(exc).__name__}"
            raise ProviderPollError(self.name, msg) from exc
        if resp.status_code != 200 or resp.text.strip() != "Ok.":
            # 403 here is qBittorrent's ban after repeated failures.
            why = (
                "IP banned for failed logins" if resp.status_code == 403 else "credentials rejected"
            )
            raise ProviderPollError(self.name, f"POST /api/v2/auth/login: {why}")
        self._signed_in = True

    async def _request(self, path: str, *, json_body: bool = True, **params: str) -> Any:  # noqa: ANN401
        if self._client is None:
            raise ProviderPollError(self.name, "provider not started")
        if not self._signed_in:
            await self._login()
        resp = await self._get(path, params)
        if resp.status_code == 403 and self.config.username is not None:
            self._signed_in = False  # the session expired: sign in once more
            await self._login()
            resp = await self._get(path, params)
        if resp.status_code == 403:
            raise ProviderPollError(
                self.name, f"GET {path}: 403 — sign-in required (set username and password)"
            )
        if resp.status_code >= 300:
            raise ProviderPollError(self.name, f"GET {path}: HTTP {resp.status_code}")
        if not json_body:
            return resp.text
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderPollError(self.name, f"GET {path}: response is not JSON") from exc

    async def _get(self, path: str, params: dict[str, str]) -> httpx.Response:
        assert self._client is not None
        try:
            return await self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise ProviderPollError(self.name, f"GET {path}: timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderPollError(self.name, f"GET {path}: {type(exc).__name__}") from exc


def _metric(uid: str, name: str, value: float, unit: SourceUnit, now: datetime) -> Metric:
    return Metric.from_source(
        resource_uid=uid, name=name, value=float(value), source_unit=unit, ts=now
    )


__all__ = ["Qbittorrent", "QbittorrentConfig", "torrent_state"]
