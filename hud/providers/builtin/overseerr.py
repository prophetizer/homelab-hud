# SPDX-License-Identifier: Apache-2.0
"""Overseerr / Jellyseerr — media requests with titles and posters (§12, media extras).

Both speak the same API. The request list carries only TMDB ids, so each title is looked up
once (``/api/v1/movie/{id}`` or ``/api/v1/tv/{id}``) and remembered: a film's name and
poster do not change between polls.

Resources:
- ``{provider}:service:main`` — version, with pending / approved / processing / available
  counts from ``/api/v1/request/count`` as metrics;
- ``{provider}:request:{movie|tv}-{tmdbId}[-4k]`` — the most recent requests. The uid is
  the media, not the request id, which is per-instance (invariant 8).

A request waiting for approval is **degraded** — someone has to act; a failed one is
**down**; available is **up**; approved, processing and declined are **unknown** —
in progress or settled, nothing to judge.

Posters: ``posters: service`` (default) fetches them through Overseerr's own
``/imageproxy/tmdb/…`` with this provider's key, so HUD talks to nothing new;
``posters: tmdb`` fetches from image.tmdb.org directly (a fixed host, never a caller's
choice); ``none`` shows none. Read-only: GET only.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from pydantic import Field, SecretStr, field_validator

from hud.config.schemas.duration import parse_duration
from hud.providers.base import HttpOptions
from hud.providers.images import fetch_image, safe_image_path
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
TMDB_IMAGES = "https://image.tmdb.org"
POSTER_SIZE = "w300"
BACKDROP_SIZE = "w780"
DETAILS_CACHE = 500

# MediaRequestStatus and MediaStatus (Overseerr/Jellyseerr server/constants/media.ts).
_REQUEST_STATUS = {1: "Pending approval", 2: "Approved", 3: "Declined", 4: "Failed", 5: "Completed"}
_MEDIA_STATUS = {
    1: "Unknown",
    2: "Pending",
    3: "Processing",
    4: "Partially available",
    5: "Available",
    6: "Deleted",
}
COUNTS = ("total", "movie", "tv", "pending", "approved", "declined", "processing", "available")


class OverseerrConfig(PluginConfig):
    base_url: str
    api_key: SecretStr
    title: str = "Overseerr"
    take: int = Field(default=20, ge=1, le=100)
    posters: Literal["service", "tmdb", "none"] = "service"
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


def request_state(request_status: object, media_status: object) -> State:
    """See the module docstring. Media availability wins over the request's own status."""
    if media_status in (4, 5):
        return State.UP
    if request_status == 1:
        return State.DEGRADED
    if request_status == 4:
        return State.DOWN
    if request_status == 5:
        return State.UP
    return State.UNKNOWN


@register("overseerr", config_model=OverseerrConfig)
class Overseerr(PluginProvider):
    config: OverseerrConfig

    def __init__(self, ctx: ProviderContext, config: OverseerrConfig, schedule: Schedule) -> None:
        super().__init__(ctx, config, schedule)
        self._client: httpx.AsyncClient | None = None
        self._tmdb: httpx.AsyncClient | None = None
        self._version: str | None = None
        self._details: OrderedDict[tuple[str, int], dict[str, Any]] = OrderedDict()

    @property
    def service_uid(self) -> str:
        return make_uid(self.name, "service", SERVICE_ID)

    async def startup(self) -> None:
        self._client = self.ctx.new_http_client(
            HttpOptions(
                base_url=self.config.base_url,
                timeout=float(parse_duration(self.config.timeout)),
                verify_tls=self.config.verify_tls,
                headers={
                    "Accept": "application/json",
                    "X-Api-Key": self.config.api_key.get_secret_value(),
                },
            )
        )
        if self.config.posters == "tmdb":
            # A fixed public host, no credentials: the key never goes to TMDB.
            self._tmdb = httpx.AsyncClient(
                base_url=TMDB_IMAGES, timeout=10.0, follow_redirects=False
            )

    async def shutdown(self) -> None:
        for client in (self._client, self._tmdb):
            if client is not None:
                await client.aclose()
        self._client = self._tmdb = None

    async def discover(self) -> list[Resource]:
        status = await self._get("/api/v1/status")
        self._version = str(status.get("version")) if isinstance(status, dict) else None
        return [self._service(datetime.now(UTC), status if isinstance(status, dict) else {})]

    async def collect(self, resources: list[Resource]) -> PollResult:
        now = datetime.now(UTC)
        counts = await self._get("/api/v1/request/count")
        page = await self._get(
            "/api/v1/request", take=str(self.config.take), skip="0", sort="added", filter="all"
        )
        results = page.get("results") if isinstance(page, dict) else None
        if not isinstance(results, list):
            raise ProviderPollError(self.name, "/api/v1/request: no results list in response")
        requests = [r for r in results if isinstance(r, dict) and isinstance(r.get("media"), dict)]
        sem = asyncio.Semaphore(4)

        async def details(req: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                return await self._media_details(req)

        looked_up = await asyncio.gather(*(details(r) for r in requests))
        service = self._service(now, {})
        out = PollResult(resources=[service])
        seen: set[str] = set()
        for req, info in zip(requests, looked_up, strict=True):
            res = self._request(req, info, now)
            if res is not None and res.uid not in seen:  # newest request per media wins
                seen.add(res.uid)
                out.resources.append(res)
        if isinstance(counts, dict):
            for name in COUNTS:
                v = counts.get(name)
                if isinstance(v, int | float) and not isinstance(v, bool):
                    out.metrics.append(
                        Metric.from_source(
                            resource_uid=service.uid,
                            name=f"requests_{name}",
                            value=float(v),
                            source_unit=SourceUnit.COUNT,
                            ts=now,
                        )
                    )
        return out

    async def fetch_image(self, path: str) -> tuple[bytes, str] | None:
        """``path`` is attrs.image or attrs.backdrop: a TMDB file path (``/abc.jpg``) with
        the size prefixed by the mapper, e.g. ``/w300/abc.jpg``."""
        if not safe_image_path(path):
            return None
        if self.config.posters == "service" and self._client is not None:
            return await fetch_image(self._client, f"/imageproxy/tmdb/t/p{path}")
        if self.config.posters == "tmdb" and self._tmdb is not None:
            return await fetch_image(self._tmdb, f"/t/p{path}")
        return None

    # ---------------------------------------------------------------- mapping

    def _service(self, now: datetime, status: dict[str, Any]) -> Resource:
        attrs: dict[str, object] = {"version": self._version}
        if "updateAvailable" in status:
            attrs["update_available"] = bool(status["updateAvailable"])
        return Resource(
            uid=self.service_uid,
            provider=self.name,
            kind="service",
            name=self.config.title,
            state=State.UP,
            attrs=attrs,
            links={"ui": self.config.base_url},
            fetched_at=now,
        )

    def _request(self, req: dict[str, Any], info: dict[str, Any], now: datetime) -> Resource | None:
        media = req["media"]
        media_type = req.get("type") or media.get("mediaType")
        tmdb = media.get("tmdbId")
        if media_type not in ("movie", "tv") or not isinstance(tmdb, int):
            return None
        native = f"{media_type}-{tmdb}" + ("-4k" if req.get("is4k") else "")
        title = info.get("title") or info.get("name") or f"TMDB {tmdb}"
        date = info.get("releaseDate") or info.get("firstAirDate") or ""
        raw_by = req.get("requestedBy")
        by: dict[str, Any] = raw_by if isinstance(raw_by, dict) else {}
        poster, backdrop = info.get("posterPath"), info.get("backdropPath")
        show = self.config.posters != "none"
        return Resource(
            uid=make_uid(self.name, "request", native),
            provider=self.name,
            kind="request",
            name=str(title),
            state=request_state(req.get("status"), media.get("status")),
            parent_uid=self.service_uid,
            attrs={
                "media_type": media_type,
                "year": date[:4] or None,
                "request_status": _text(_REQUEST_STATUS, req.get("status")),
                "media_status": _text(_MEDIA_STATUS, media.get("status")),
                "requested_by": by.get("displayName"),
                "requested_at": req.get("createdAt"),
                "is4k": bool(req.get("is4k")),
                "image": f"/{POSTER_SIZE}{poster}" if show and _file(poster) else None,
                "backdrop": f"/{BACKDROP_SIZE}{backdrop}" if show and _file(backdrop) else None,
            },
            fetched_at=now,
        )

    async def _media_details(self, req: dict[str, Any]) -> dict[str, Any]:
        """Title, date and artwork for a request's media, looked up once and remembered. A
        failed lookup is an untitled row this poll, not a failed provider."""
        media = req["media"]
        media_type = req.get("type") or media.get("mediaType")
        tmdb = media.get("tmdbId")
        if media_type not in ("movie", "tv") or not isinstance(tmdb, int):
            return {}
        key = (str(media_type), tmdb)
        if key in self._details:
            self._details.move_to_end(key)
            return self._details[key]
        try:
            body = await self._get(f"/api/v1/{media_type}/{tmdb}")
        except ProviderPollError as exc:
            self.log.warning("no details for %s %s: %s", media_type, tmdb, exc)
            return {}
        info = {
            k: body.get(k)
            for k in ("title", "name", "releaseDate", "firstAirDate", "posterPath", "backdropPath")
            if isinstance(body, dict)
        }
        self._details[key] = info
        while len(self._details) > DETAILS_CACHE:
            self._details.popitem(last=False)
        return info

    async def _get(self, path: str, **params: str) -> Any:  # noqa: ANN401
        if self._client is None:
            raise ProviderPollError(self.name, "provider not started")
        try:
            resp = await self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise ProviderPollError(self.name, f"GET {path}: timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderPollError(self.name, f"GET {path}: {type(exc).__name__}") from exc
        if resp.status_code in (401, 403):
            raise ProviderPollError(self.name, f"GET {path}: {resp.status_code} — API key rejected")
        if resp.status_code >= 300:
            raise ProviderPollError(self.name, f"GET {path}: HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderPollError(self.name, f"GET {path}: response is not JSON") from exc


def _text(names: dict[int, str], code: object) -> str:
    return names.get(code, "Unknown") if isinstance(code, int) else "Unknown"


def _file(path: object) -> bool:
    """A TMDB file path: ``/`` then a plain file name."""
    return isinstance(path, str) and path.startswith("/") and "/" not in path[1:] and len(path) > 1


__all__ = ["Overseerr", "OverseerrConfig", "request_state"]
