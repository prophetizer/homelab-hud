# SPDX-License-Identifier: Apache-2.0
"""Last checks at the provider boundary before anything reaches the cache or the store.

Tier 1 has already mapped and unit-converted; Tier 2 plugins construct canonical objects
themselves. Either way, a provider may only speak for its own uids: a resource or metric
claiming another provider's namespace is dropped here, so a misbehaving plugin can fork
neither another provider's history nor its tiles (invariants 6 and 8).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from hud.models import Event, Metric, Resource
from hud.providers.base import PollResult

log = logging.getLogger(__name__)


@dataclass
class Normalized:
    resources: list[Resource] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    dropped: int = 0


def normalize(provider: str, result: PollResult) -> Normalized:
    """Keep only objects inside ``provider``'s namespace; metrics and events must also
    refer to a resource present in the same result or already known by uid prefix."""
    out = Normalized()
    prefix = f"{provider}:"
    uids: set[str] = set()
    for r in result.resources:
        if r.provider != provider or not r.uid.startswith(prefix):
            out.dropped += 1
            log.warning("%s: dropped resource %r outside provider namespace", provider, r.uid)
            continue
        if r.uid in uids:
            out.dropped += 1
            log.warning("%s: dropped duplicate resource %r", provider, r.uid)
            continue
        uids.add(r.uid)
        out.resources.append(r)
    for m in result.metrics:
        if not m.resource_uid.startswith(prefix):
            out.dropped += 1
            log.warning("%s: dropped metric %s, foreign uid %r", provider, m.name, m.resource_uid)
            continue
        out.metrics.append(m)
    for e in result.events:
        if not e.resource_uid.startswith(prefix):
            out.dropped += 1
            log.warning("%s: dropped event %s, foreign uid %r", provider, e.type, e.resource_uid)
            continue
        out.events.append(e)
    return out
