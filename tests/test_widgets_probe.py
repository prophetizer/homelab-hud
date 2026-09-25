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


# ------------------------------------------------ verdicts for the page that is asking

HUD = "https://hud.lab.example"
# Grafana's actual header on the live stack, after Traefik's organizr-frame middleware.
GRAFANA_FA = "frame-ancestors 'self' https://organizr.lab.example https://hud.lab.example"


def _v(csp: str = "", xfo: str = "", url: str = "https://grafana.lab.example/", page: str = HUD):
    headers = {}
    if csp:
        headers["Content-Security-Policy"] = csp
    if xfo:
        headers["X-Frame-Options"] = xfo
    return verdict_from_headers(httpx.Headers(headers), url=url, page_origin=page)


def test_frame_ancestors_listing_the_hud_origin_allows_it() -> None:
    """Found live: the proxy listed https://hud.… and the old probe, not knowing its own
    origin, still reported the app as refusing to be framed."""
    assert _v(GRAFANA_FA).allowed is True
    elsewhere = _v(GRAFANA_FA, page="https://other.example")
    assert elsewhere.allowed is False
    assert "does not include https://other.example" in elsewhere.reason
    assert "add it at the proxy" in elsewhere.reason


def test_http_app_on_an_https_page_is_mixed_content() -> None:
    """Found live: Node-RED on http:// framed from https://hud.… is a blank frame."""
    v = _v(url="http://172.31.1.10:1880/")
    assert v.allowed is False and "mixed content" in v.reason
    assert _v(url="http://172.31.1.10:1880/", page="http://hud.lan").allowed is True


@pytest.mark.parametrize(
    ("csp", "page", "allowed"),
    [
        ("frame-ancestors 'self'", "https://grafana.lab.example", True),
        ("frame-ancestors 'self'", HUD, False),
        ("frame-ancestors 'none'", HUD, False),
        ("frame-ancestors *", HUD, True),
        ("frame-ancestors https:", HUD, True),
        ("frame-ancestors https:", "http://hud.lan", False),
        ("frame-ancestors https://*.lab.example", HUD, True),
        ("frame-ancestors https://*.lab.example", "https://lab.example", False),
        ("frame-ancestors hud.lab.example", HUD, True),  # scheme-less host source
        ("frame-ancestors http://hud.lab.example", HUD, True),  # http source, https page
        ("frame-ancestors https://hud.lab.example", "http://hud.lab.example", False),
        ("frame-ancestors https://hud.lab.example:8443", HUD, False),
        ("frame-ancestors https://hud.lab.example:8443", HUD + ":8443", True),
        ("frame-ancestors https://hud.lab.example:*", HUD + ":8443", True),
        ("frame-ancestors https://HUD.lab.example/some/path", HUD, True),  # case, path
    ],
)
def test_frame_ancestors_source_matching(csp: str, page: str, allowed: bool) -> None:
    assert _v(csp, page=page).allowed is allowed


def test_frame_ancestors_overrides_x_frame_options() -> None:
    # Browsers ignore X-Frame-Options when frame-ancestors is present.
    assert _v(GRAFANA_FA, xfo="DENY").allowed is True


def test_sameorigin_depends_on_the_page() -> None:
    assert _v(xfo="SAMEORIGIN", page="https://grafana.lab.example").allowed is True
    assert _v(xfo="SAMEORIGIN").allowed is False


@respx.mock
async def test_one_fetch_serves_different_verdicts_per_page() -> None:
    route = respx.get("https://grafana.lab.example/").mock(
        return_value=httpx.Response(200, headers={"Content-Security-Policy": GRAFANA_FA})
    )
    prober = FramingProber()
    ok, other = await asyncio.gather(
        prober.probe("https://grafana.lab.example/", HUD),
        prober.probe("https://grafana.lab.example/", "https://other.example"),
    )
    assert ok.allowed is True and other.allowed is False
    assert route.call_count == 1  # the fetch is shared; only the verdict differs
    assert prober.cached("https://grafana.lab.example/", HUD).allowed is True  # type: ignore[union-attr]
