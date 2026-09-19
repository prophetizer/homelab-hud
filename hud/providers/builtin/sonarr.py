# SPDX-License-Identifier: Apache-2.0
"""Sonarr plugin — the first ``*arr`` provider (PLAN.md §12 Phase 1).

Resources: one ``service`` (``sonarr:service:main``) carrying version and health, plus
one ``download`` per queue record, parented to the service. Metrics: ``queue_size`` and
``queue_bytes_left`` on the service, ``progress_pct`` per download. Health items become
``health`` events the first time they are seen and a ``resolved`` event when they clear,
so the events feed shows changes rather than repeating every poll. Read-only: only GET.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import Field, SecretStr, field_validator

from hud.config.schemas.settings import parse_duration
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
SERVICE_ID = "main"


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
        queue = await self._get("/api/v3/queue", pageSize=page_size, page="1")
        records = queue.get("records") if isinstance(queue, dict) else None
        if not isinstance(records, list):
            raise ProviderPollError(self.name, "/api/v3/queue: no records list in response")
        service = self._service(health, now)
        result = PollResult(resources=[service], events=self._health_events(health, now))
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
