# SPDX-License-Identifier: Apache-2.0
"""Framing probe: header rules, caching, honest 'unreachable'."""

import asyncio

import httpx
import pytest
import respx

from hud.widgets.probe import FramingProber, verdict_from_headers


@pytest.mark.parametrize(
    ("headers", "allowed", "reason"),
    [
        ({}, True, "no framing restriction header"),
        ({"X-Frame-Options": "DENY"}, False, "X-Frame-Options: DENY"),
        ({"x-frame-options": "sameorigin"}, False, "X-Frame-Options: SAMEORIGIN"),
        ({"Content-Security-Policy": "frame-ancestors *"}, True, "frame-ancestors *"),
        (
            {"Content-Security-Policy": "default-src 'self'; frame-ancestors 'self' https://a.b"},
            False,
            "Content-Security-Policy: frame-ancestors 'self' https://a.b",
        ),
        ({"Content-Security-Policy": "frame-ancestors 'none'"}, False, "frame-ancestors 'none'"),
        ({"Content-Security-Policy": "default-src 'self'"}, True, "no framing restriction header"),
    ],
)
def test_verdict_from_headers(headers: dict[str, str], allowed: bool, reason: str) -> None:
    v = verdict_from_headers(httpx.Headers(headers))
    assert v.allowed is allowed
    assert reason in v.reason


@respx.mock
async def test_probe_caches_and_dedupes_inflight() -> None:
    route = respx.get("http://app.lab/").mock(
        return_value=httpx.Response(200, headers={"X-Frame-Options": "DENY"}, text="<html>")
    )
    prober = FramingProber()
    assert prober.cached("http://app.lab/") is None
    a, b = await asyncio.gather(prober.probe("http://app.lab/"), prober.probe("http://app.lab/"))
    assert a.allowed is False and a == b
    assert route.call_count == 1  # concurrent callers shared one request
    assert (await prober.probe("http://app.lab/")).allowed is False
    assert route.call_count == 1  # cached
    prober.invalidate("http://app.lab/")
    await prober.probe("http://app.lab/")
    assert route.call_count == 2


@respx.mock
async def test_probe_follows_redirect_and_reports_http_errors() -> None:
    respx.get("http://app.lab/").mock(
        return_value=httpx.Response(302, headers={"location": "http://app.lab/login"})
    )
    respx.get("http://app.lab/login").mock(
        return_value=httpx.Response(401, headers={"Content-Security-Policy": "frame-ancestors *"})
    )
    v = await FramingProber().probe("http://app.lab/")
    assert v.allowed is True
    assert v.reason == "HTTP 401; frame-ancestors *"


@respx.mock
async def test_unreachable_is_unknown_not_blocked() -> None:
    respx.get("http://down.lab/").mock(side_effect=httpx.ConnectError("refused"))
    v = await FramingProber().probe("http://down.lab/")
    assert v.allowed is None
    assert v.reason.startswith("unreachable:")
