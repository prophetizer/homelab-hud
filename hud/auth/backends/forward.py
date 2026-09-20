# SPDX-License-Identifier: Apache-2.0
"""Trusted-proxy header identity (PLAN.md §10.1).

Fails closed: headers are honoured only when the *immediate* peer is inside
``trusted_proxies`` **and** the user header is present. Anything else yields no identity, so
a request that bypasses the proxy is anonymous rather than whoever it claims to be.
"""

from __future__ import annotations

import ipaddress
import logging

from starlette.requests import Request

from hud.auth.backends.base import ExternalIdentity
from hud.config.schemas.settings import ForwardAuthSettings

log = logging.getLogger(__name__)


class ForwardBackend:
    def __init__(self, settings: ForwardAuthSettings) -> None:
        self.settings = settings
        self._networks = [ipaddress.ip_network(n, strict=False) for n in settings.trusted_proxies]

    def peer_is_trusted(self, request: Request) -> bool:
        if request.client is None:
            return False
        try:
            addr = ipaddress.ip_address(request.client.host)
        except ValueError:
            return False
        return any(addr in net for net in self._networks)

    def identify(self, request: Request) -> ExternalIdentity | None:
        if not self.peer_is_trusted(request):
            return None
        s = self.settings
        user = request.headers.get(s.header_user, "").strip()
        if not user:
            return None
        raw_groups = request.headers.get(s.header_groups, "")
        groups = frozenset(g.strip() for g in raw_groups.split(s.groups_separator) if g.strip())
        return ExternalIdentity(
            subject=user,
            source="forward",
            display_name=request.headers.get(s.header_name, "").strip() or None,
            email=request.headers.get(s.header_email, "").strip() or None,
            groups=groups,
        )


__all__ = ["ForwardBackend"]
