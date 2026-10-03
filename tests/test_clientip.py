# SPDX-License-Identifier: Apache-2.0
"""The client address sign-in limits and the audit log use: behind a trusted proxy, the
right-most X-Forwarded-For hop that is not a proxy; from anyone else, the connection —
whatever header they send."""

from starlette.requests import Request

from hud.auth.clientip import client_ip, networks

TRAEFIK = networks(["172.31.90.0/24"])


def request(peer: str, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return Request({"type": "http", "client": (peer, 51000), "headers": headers})


def test_behind_a_trusted_proxy_the_client_is_the_last_untrusted_hop() -> None:
    assert client_ip(request("172.31.90.56", "203.0.113.9"), TRAEFIK) == "203.0.113.9"
    # A client-supplied entry on the left is not believed over what the proxy appended.
    assert client_ip(request("172.31.90.56", "10.9.9.9, 203.0.113.9"), TRAEFIK) == "203.0.113.9"
    # Hops that are themselves trusted proxies are skipped.
    assert client_ip(request("172.31.90.56", "203.0.113.9, 172.31.90.7"), TRAEFIK) == "203.0.113.9"


def test_anyone_else_is_their_connection_whatever_they_claim() -> None:
    assert client_ip(request("203.0.113.9", "127.0.0.1"), TRAEFIK) == "203.0.113.9"
    assert client_ip(request("172.31.90.56", "203.0.113.9"), []) == "172.31.90.56"  # none trusted


def test_a_missing_or_garbled_header_falls_back_to_the_proxy() -> None:
    assert client_ip(request("172.31.90.56"), TRAEFIK) == "172.31.90.56"
    assert client_ip(request("172.31.90.56", "not-an-ip"), TRAEFIK) == "172.31.90.56"
