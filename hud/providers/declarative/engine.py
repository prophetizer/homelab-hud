# SPDX-License-Identifier: Apache-2.0
"""The Tier 1 provider: a validated ``kind: Provider`` document, running (PLAN.md §7.1).

One poll group per ``resources[]`` entry. A poll fetches the page(s), applies ``select``,
maps every item, and returns a :class:`PollResult`. A single unmappable item is logged and
skipped; if *every* item fails the poll fails, because a broken template must not look
like an empty service.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from hud.config.schemas import ProviderDocument, ResourceSpec
from hud.config.schemas.duration import parse_duration
from hud.providers.base import PollGroup, PollResult, Provider, ProviderContext, ProviderTier
from hud.providers.declarative.mapper import ItemError, ResourceMapper
from hud.providers.declarative.select import SelectError, Selector
from hud.providers.declarative.transport import (
    CompiledRequest,
    build_http_options,
    fetch_pages,
)
from hud.providers.errors import ProviderBuildError, ProviderPollError
from hud.providers.images import fetch_image

MAX_ITEM_ERRORS_LOGGED = 3


@dataclass(frozen=True)
class _Group:
    spec: ResourceSpec
    request: CompiledRequest
    select: Selector
    mapper: ResourceMapper
    poll: PollGroup


class DeclarativeProvider(Provider):
    tier = ProviderTier.DECLARATIVE

    def __init__(self, ctx: ProviderContext, doc: ProviderDocument) -> None:
        super().__init__(ctx, doc.metadata.labels)
        self.doc = doc
        if doc.spec.transport is None:  # the schema guarantees this for Tier 1 documents
            raise ProviderBuildError(ctx.name, "not a declarative provider (no transport)")
        self._http = build_http_options(ctx, doc.spec.transport)
        self._client: httpx.AsyncClient | None = None
        self._groups: dict[str, _Group] = {}
        defaults = doc.spec.defaults
        for res in doc.spec.resources:
            try:
                select = Selector(res.select)
            except SelectError as exc:
                raise ProviderBuildError(ctx.name, f"resource {res.name!r}: {exc}") from exc
            interval = parse_duration(res.interval) if res.interval else defaults.interval_seconds
            jitter = parse_duration(res.jitter) if res.jitter else defaults.jitter_seconds
            # Room for every page plus mapping before the scheduler gives up on us.
            pages = res.paginate.max_pages if res.paginate else 1
            timeout = self._http.timeout * pages + 5.0
            self._groups[res.name] = _Group(
                spec=res,
                request=CompiledRequest.build(ctx, res.request, res.paginate),
                select=select,
                mapper=ResourceMapper(ctx.name, res.map, res.metrics, ctx.env, ctx.log),
                poll=PollGroup(res.name, float(interval), float(jitter), timeout),
            )

    @property
    def kinds(self) -> set[str]:
        return {g.spec.map.kind for g in self._groups.values()}

    def groups(self) -> Sequence[PollGroup]:
        return [g.poll for g in self._groups.values()]

    async def startup(self) -> None:
        self._client = self.ctx.new_http_client(self._http)

    async def fetch_image(self, path: str) -> tuple[bytes, str] | None:
        if self._client is None:
            return None
        return await fetch_image(self._client, path)

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def poll(self, group: str) -> PollResult:
        g = self._groups[group]
        if self._client is None:
            raise ProviderPollError(self.name, "provider not started")
        fetched_at = datetime.now(UTC)
        result = PollResult()
        seen: set[str] = set()
        errors: list[str] = []
        total = 0
        async for page in fetch_pages(self._client, self.name, g.request):
            items = g.select.items(page)
            if not items:
                break  # an empty page ends pagination; closing the generator stops fetching
            for item in items:
                total += 1
                try:
                    resource, metrics = g.mapper.map_item(item, fetched_at)
                except ItemError as exc:
                    errors.append(str(exc))
                    continue
                if resource.uid in seen and g.mapper.uid_on_collision is not None:
                    # Two items for one thing (two queue rows for one episode): the second
                    # shows under its own uid rather than being dropped.
                    try:
                        resource, metrics = g.mapper.map_item(item, fetched_at, collision=True)
                    except ItemError as exc:
                        errors.append(str(exc))
                        continue
                if resource.uid in seen:
                    errors.append(f"duplicate uid {resource.uid!r}")
                    continue
                seen.add(resource.uid)
                result.resources.append(resource)
                result.metrics.extend(metrics)
        if errors:
            for e in errors[:MAX_ITEM_ERRORS_LOGGED]:
                self.log.warning("%s: item skipped: %s", group, e)
            more = len(errors) - MAX_ITEM_ERRORS_LOGGED
            if more > 0:
                self.log.warning("%s: %d more items skipped", group, more)
            if not result.resources and total:
                msg = f"{group}: all {total} items failed to map; first error: {errors[0]}"
                raise ProviderPollError(self.name, msg)
        return result


def build_declarative(doc: ProviderDocument, ctx: ProviderContext) -> Provider:
    """Registry factory for Tier 1 documents."""
    return DeclarativeProvider(ctx, doc)
