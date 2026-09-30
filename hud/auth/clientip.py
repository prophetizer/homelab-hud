# SPDX-License-Identifier: Apache-2.0
"""The client's address, as sign-in limits and the audit log should see it.

Behind a reverse proxy every connection comes from the proxy, so a per-address limit would
be one bucket for everyone — ten bad guesses from anywhere would lock the whole instance out.
With ``auth.trusted_proxies`` set, a request whose connection comes from a trusted proxy is
attributed to the right-most ``X-Forwarded-For`` hop that is not itself a trusted proxy
(the left-most entries are client-supplied and never believed on their own). A request from
anywhere else is its connection address, whatever headers it carries.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence

from fastapi import Request

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def networks(cidrs: Sequence[str]) -> list[Network]:
    return [ipaddress.ip_network(c, strict=False) for c in cidrs]


def _trusted(address: str, trusted: Sequence[Network]) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in net for net in trusted)


def client_ip(request: Request, trusted: Sequence[Network]) -> str:
    peer = request.client.host if request.client else "unknown"
    if not trusted or not _trusted(peer, trusted):
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):
        if not _trusted(hop, trusted):
            try:
                return str(ipaddress.ip_address(hop))
            except ValueError:
                return peer  # a malformed hop from a trusted proxy: fall back, never guess
    return peer


__all__ = ["client_ip", "networks"]
