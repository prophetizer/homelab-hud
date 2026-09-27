# SPDX-License-Identifier: Apache-2.0
"""Sonarr plugin — the first ``*arr`` provider (PLAN.md §12 Phase 1).

Resources: one ``service`` (``sonarr:service:main``) carrying version and health, plus
one ``download`` per queue record, parented to the service. Metrics: ``queue_size`` and
``queue_bytes_left`` on the service, ``progress_pct`` per download. Health items become
``health`` events the first time they are seen and a ``resolved`` event when they clear,
so the events feed shows changes rather than repeating every poll. Read-only: only GET.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import Field, SecretStr, field_validator

from hud.config.schemas.duration import parse_duration
from hud.providers.images import fetch_image
from hud.providers.sdk import (
    Event,
    HttpOptions,
    Metric,
    PluginConfig,
    PluginProvider,
    PollResult,
    ProviderContext,
    ProviderPollError,
    Resource,
    Schedule,
    Severity,
    SourceUnit,
    State,
    make_uid,
    register,
)

SERVICE_KIND = "service"
DOWNLOAD_KIND = "download"
UPCOMING_KIND = "upcoming"
SERVICE_ID = "main"
CALENDAR_EVERY = 900.0  # seconds between calendar fetches; episodes do not move often
CALENDAR_DAYS = 14


class SonarrConfig(PluginConfig):
    base_url: str
    api_key: SecretStr
    name: str = "Sonarr"
    timeout: str = "10s"
    verify_tls: bool = True
    queue_page_size: int = Field(default=100, ge=1, le=1000)

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

    @property
    def timeout_seconds(self) -> float:
        return float(parse_duration(self.timeout))


# Queue record status/state → canonical state. Anything unlisted is 'unknown'.
_DOWNLOAD_STATE: dict[str, State] = {
    "downloading": State.UP,
    "queued": State.UP,
    "completed": State.UP,
    "delay": State.UP,
    "paused": State.PAUSED,
    "warning": State.DEGRADED,
    "failed": State.DOWN,
}
_HEALTH_SEVERITY: dict[str, Severity] = {
    "error": Severity.ERROR,
    "warning": Severity.WARN,
    "notice": Severity.INFO,
}


@register("sonarr", config_model=SonarrConfig)
class SonarrProvider(PluginProvider):
    config: SonarrConfig

    def __init__(self, ctx: ProviderContext, config: SonarrConfig, schedule: Schedule) -> None:
        super().__init__(ctx, config, schedule)
        self._client: httpx.AsyncClient | None = None
        self._status: dict[str, Any] = {}
        self._health_seen: dict[tuple[str, str], Severity] = {}
        self._calendar: list[dict[str, Any]] = []
        self._calendar_at = float("-inf")

    @property
    def service_uid(self) -> str:
        return make_uid(self.name, SERVICE_KIND, SERVICE_ID)

    async def startup(self) -> None:
        self._client = self.ctx.new_http_client(
            HttpOptions(
                base_url=self.config.base_url,
                timeout=self.config.timeout_seconds,
                verify_tls=self.config.verify_tls,
                headers={
                    "Accept": "application/json",
                    "X-Api-Key": self.config.api_key.get_secret_value(),
                },
            )
        )

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---------------------------------------------------------------- contract

    async def discover(self) -> list[Resource]:
        self._status = await self._get("/api/v3/system/status")
        return [self._service(await self._health_items(), datetime.now(UTC))]

    async def collect(self, resources: list[Resource]) -> PollResult:
        now = datetime.now(UTC)
        health = await self._health_items()
        page_size = str(self.config.queue_page_size)
        queue = await self._get("/api/v3/queue", pageSize=page_size, page="1", includeSeries="true")
        records = queue.get("records") if isinstance(queue, dict) else None
        if not isinstance(records, list):
            raise ProviderPollError(self.name, "/api/v3/queue: no records list in response")
        service = self._service(health, now)
        result = PollResult(resources=[service], events=self._health_events(health, now))
        for ep in await self._calendar_items(now):
            upcoming = self._upcoming(ep, now)
            if upcoming is not None:
                result.resources.append(upcoming)
        bytes_left = 0.0
        for rec in records:
            if not isinstance(rec, dict):
                continue
            try:
                download, progress = self._download(rec, now)
            except (KeyError, ValueError, TypeError) as exc:
                self.log.warning("skipping queue record %r: %s", rec.get("id"), exc)
                continue
            result.resources.append(download)
            bytes_left += float(rec.get("sizeleft") or 0)
            if progress is not None:
                result.metrics.append(
                    Metric.from_source(
                        resource_uid=download.uid,
                        name="progress_pct",
                        value=progress,
                        source_unit=SourceUnit.PCT,
                        ts=now,
                    )
                )
        for name, kind in (("health_errors", "error"), ("health_warnings", "warning")):
            result.metrics.append(
                Metric.from_source(
                    resource_uid=service.uid,
                    name=name,
                    value=float(sum(1 for h in health if h.get("type") == kind)),
                    source_unit=SourceUnit.COUNT,
                    ts=now,
                )
            )
        total = queue.get("totalRecords", len(result.resources) - 1)
        result.metrics.append(
            Metric.from_source(
                resource_uid=service.uid,
                name="queue_size",
                value=float(total),
                source_unit=SourceUnit.COUNT,
                ts=now,
            )
        )
        result.metrics.append(
            Metric.from_source(
                resource_uid=service.uid,
                name="queue_bytes_left",
                value=bytes_left,
                source_unit=SourceUnit.BYTES,
                ts=now,
            )
        )
        return result

    # ---------------------------------------------------------------- mapping

    def _service(self, health: list[dict[str, Any]], now: datetime) -> Resource:
        errors = [h for h in health if h.get("type") == "error"]
        warnings = [h for h in health if h.get("type") == "warning"]
        s = self._status
        attrs: dict[str, Any] = {
            "app": s.get("appName", "Sonarr"),
            "version": s.get("version"),
            "branch": s.get("branch"),
            "os": " ".join(str(x) for x in (s.get("osName"), s.get("osVersion")) if x) or None,
            "start_time": s.get("startTime"),
            "health_errors": len(errors),
            "health_warnings": len(warnings),
            "health_messages": [str(h.get("message")) for h in errors + warnings][:10],
        }
        return Resource(
            uid=self.service_uid,
            provider=self.name,
            kind=SERVICE_KIND,
            name=self.config.name,
            state=State.DEGRADED if errors else State.UP,
            attrs={k: v for k, v in attrs.items() if v is not None},
            links={"ui": self.config.base_url},
            fetched_at=now,
        )

    def _download(self, rec: dict[str, Any], now: datetime) -> tuple[Resource, float | None]:
        rec_id = int(rec["id"])
        series = rec.get("series") or {}
        episode = rec.get("episode") or {}
        title = str(rec.get("title") or series.get("title") or f"queue item {rec_id}")
        status = str(rec.get("status") or "").lower()
        tracked = str(rec.get("trackedDownloadStatus") or "").lower()
        state = _DOWNLOAD_STATE.get(status, State.UNKNOWN)
        if tracked == "error":
            state = State.DOWN
        elif tracked == "warning" and state is State.UP:
            state = State.DEGRADED
        size = float(rec.get("size") or 0)
        left = float(rec.get("sizeleft") or 0)
        progress = (size - left) / size * 100.0 if size > 0 else None
        attrs: dict[str, Any] = {
            "series": series.get("title"),
            "season": episode.get("seasonNumber"),
            "episode": episode.get("episodeNumber"),
            "status": status,
            "tracked_status": tracked or None,
            "tracked_state": rec.get("trackedDownloadState"),
            "size_bytes": size,
            "size_left_bytes": left,
            "time_left": rec.get("timeleft"),
            "protocol": rec.get("protocol"),
            "download_client": rec.get("downloadClient"),
            "indexer": rec.get("indexer"),
            "reason": _first_message(rec),
            # Paths on this Sonarr (MediaCover), fetched with the API key by HUD's image
            # route (fetch_image below) — never handed to the browser.
            "image": _cover(series, "poster"),
            "backdrop": _cover(series, "fanart"),
        }
        resource = Resource(
            uid=make_uid(self.name, DOWNLOAD_KIND, str(rec_id)),
            provider=self.name,
            kind=DOWNLOAD_KIND,
            name=title,
            state=state,
            attrs={k: v for k, v in attrs.items() if v is not None},
            parent_uid=self.service_uid,
            fetched_at=now,
        )
        return resource, progress

    async def _calendar_items(self, now: datetime) -> list[dict[str, Any]]:
        """Episodes airing from yesterday to CALENDAR_DAYS out, refetched every
        CALENDAR_EVERY seconds; between fetches the last answer is reused."""
        if time.monotonic() - self._calendar_at >= CALENDAR_EVERY:
            raw = await self._get(
                "/api/v3/calendar",
                start=_iso(now - timedelta(days=1)),
                end=_iso(now + timedelta(days=CALENDAR_DAYS)),
                includeSeries="true",
                unmonitored="false",
            )
            self._calendar = (
                [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
            )
            self._calendar_at = time.monotonic()
        return self._calendar

    def _upcoming(self, ep: dict[str, Any], now: datetime) -> Resource | None:
        """One calendar episode, as the template maps it: downloaded is up, aired but not
        downloaded is degraded, not yet aired is unknown."""
        season, number = ep.get("seasonNumber"), ep.get("episodeNumber")
        if not isinstance(season, int) or not isinstance(number, int) or "seriesId" not in ep:
            return None
        raw_series = ep.get("series")
        series: dict[str, Any] = raw_series if isinstance(raw_series, dict) else {}
        air = ep.get("airDateUtc")
        if ep.get("hasFile"):
            state = State.UP
        elif isinstance(air, str) and air < _iso(now):
            state = State.DEGRADED
        else:
            state = State.UNKNOWN
        label = f"S{season:02d}E{number:02d}" + (f" · {ep['title']}" if ep.get("title") else "")
        return Resource(
            uid=make_uid(self.name, UPCOMING_KIND, f"{ep['seriesId']}-s{season}e{number}"),
            provider=self.name,
            kind=UPCOMING_KIND,
            name=str(series.get("title") or ep.get("title") or label),
            state=state,
            parent_uid=self.service_uid,
            attrs={
                "show": series.get("title"),
                "episode": label,
                "air_at": air,
                "network": series.get("network"),
                "has_file": bool(ep.get("hasFile")),
                "image": _cover(series, "poster"),
            },
            fetched_at=now,
        )

    async def fetch_image(self, path: str) -> tuple[bytes, str] | None:
        if self._client is None:
            return None
        return await fetch_image(self._client, path)

    def _health_events(self, health: list[dict[str, Any]], now: datetime) -> list[Event]:
        current: dict[tuple[str, str], Severity] = {}
        for h in health:
            sev = _HEALTH_SEVERITY.get(str(h.get("type", "")).lower())
            if sev is None:
                continue
            current[(str(h.get("source", "")), str(h.get("message", "")))] = sev
        events: list[Event] = []
        for key, sev in current.items():
            if key not in self._health_seen:
                events.append(self._event("health", sev, _label(key), now))
        for key in self._health_seen:
            if key not in current:
                text = _label(key) + " — resolved"
                events.append(self._event("resolved", Severity.INFO, text, now))
        self._health_seen = current
        return events

    def _event(self, type_: str, severity: Severity, message: str, now: datetime) -> Event:
        return Event(
            resource_uid=self.service_uid, type=type_, severity=severity, message=message, ts=now
        )

    # ---------------------------------------------------------------- api

    async def _health_items(self) -> list[dict[str, Any]]:
        raw = await self._get("/api/v3/health")
        return [h for h in raw if isinstance(h, dict)] if isinstance(raw, list) else []

    async def _get(self, path: str, **params: str) -> Any:  # noqa: ANN401
        if self._client is None:
            raise ProviderPollError(self.name, "provider not started")
        try:
            resp = await self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise ProviderPollError(self.name, f"GET {path}: timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderPollError(self.name, f"GET {path}: {exc}") from exc
        if resp.status_code == 401:
            raise ProviderPollError(self.name, f"GET {path}: 401 — API key rejected")
        if resp.status_code >= 300:
            raise ProviderPollError(self.name, f"GET {path}: HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            ctype = resp.headers.get("content-type", "?")
            msg = f"GET {path}: response is not JSON (content-type {ctype})"
            raise ProviderPollError(self.name, msg) from exc


def _label(key: tuple[str, str]) -> str:
    source, message = key
    return f"{source}: {message}" if source else message


# The sized copies Sonarr keeps beside each original: a tile never needs a multi-MB file,
# and HUD refuses anything over 2 MiB.
_SIZED = {
    "poster": ("/poster.jpg", "/poster-500.jpg"),
    "fanart": ("/fanart.jpg", "/fanart-360.jpg"),
}


def _cover(series: dict[str, Any], cover_type: str) -> str | None:
    """The series' image of this type as a path on Sonarr (``/MediaCover/...``), or None."""
    for image in series.get("images") or []:
        if isinstance(image, dict) and image.get("coverType") == cover_type and image.get("url"):
            original, sized = _SIZED.get(cover_type, ("", ""))
            return str(image["url"]).replace(original, sized) if original else str(image["url"])
    return None


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_message(rec: dict[str, Any]) -> str | None:
    """Why an import is stuck, in Sonarr's words: the first status message, if any."""
    for status in rec.get("statusMessages") or []:
        for message in (status or {}).get("messages") or []:
            return str(message)
    return None
