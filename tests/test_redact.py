# SPDX-License-Identifier: Apache-2.0
"""Secrets never leave HUD in text: not in logs (tracebacks included), not in provider
errors, which reach health and the UI (invariant 3). Motivated by the query auth type —
a key in a URL is what HTTP libraries log and quote — but applied to every secret."""

import logging
from pathlib import Path

import httpx
import pytest
import respx

from hud.config import SecretResolver
from hud.config.redact import MASK, RedactingFormatter, install, redact, register
from hud.providers.errors import ProviderPollError
from tests.test_providers_declarative import load_doc, make_ctx

KEY = "sab-0123456789abcdef-key"


def test_registered_values_are_masked_in_every_form() -> None:
    register(KEY)
    register("a/b+c=d&e 12345")  # a value whose URL-encoded form differs
    assert redact(f"GET http://sab.lab/api?apikey={KEY}&mode=queue") == (
        f"GET http://sab.lab/api?apikey={MASK}&mode=queue"
    )
    assert MASK in redact("apikey=a%2Fb%2Bc%3Dd%26e%2012345")  # as httpx encodes it
    register("short")  # below the minimum: would mangle text, is not a real credential
    assert redact("a short note") == "a short note"


def test_resolver_registers_what_it_hands_out(tmp_path: Path) -> None:
    value = "resolved-secret-9f8e7d6c"
    r = SecretResolver(tmp_path, tmp_path / "none", env={"HUD_SECRET_X": value})
    assert r.resolve("x") == value  # callers still get the real value
    assert redact(f"token={value}") == f"token={MASK}"


def test_provider_errors_are_masked_before_anyone_sees_them() -> None:
    register(KEY)
    err = ProviderPollError("sabnzbd", f"GET /api: cannot reach http://sab.lab/api?apikey={KEY}")
    assert KEY not in err.message and KEY not in str(err) and MASK in err.message


def test_log_formatter_masks_messages_and_tracebacks() -> None:
    register(KEY)
    fmt = RedactingFormatter("%(message)s")
    try:
        raise RuntimeError(f"boom at http://sab.lab/api?apikey={KEY}")
    except RuntimeError:
        import sys  # noqa: PLC0415

        record = logging.LogRecord(
            "t", logging.ERROR, __file__, 1, "saw %s", (KEY,), sys.exc_info()
        )
    out = fmt.format(record)
    assert KEY not in out and out.count(MASK) >= 2  # the message and the traceback


def test_install_silences_httpx_request_lines() -> None:
    # httpx logs every request's full URL at INFO — 1,098 lines in five minutes live.
    install("%(message)s")
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


QUERY_DOC = """\
apiVersion: hud/v1
kind: Provider
metadata: {name: sabnzbd}
spec:
  transport:
    base_url: http://sab.lab
    auth: {type: query, param: apikey, value: "${secret:sabnzbd_api_key}"}
  defaults: {interval: 1h, jitter: 0s}
  resources:
    - name: queue
      request: {path: /api, params: {mode: queue, output: json}}
      select: "$.queue"
      map: {uid: "sabnzbd:queue:main", kind: queue, name: SABnzbd, state: up}
"""


@respx.mock
async def test_query_auth_sends_the_key_and_never_reports_it(config_dir: Path) -> None:
    route = respx.get("http://sab.lab/api").mock(
        return_value=httpx.Response(200, json={"queue": {}})
    )
    env = {"HUD_SECRET_SABNZBD_API_KEY": KEY}
    from hud.providers.declarative import build_declarative  # noqa: PLC0415

    p = build_declarative(load_doc(config_dir, QUERY_DOC), make_ctx(config_dir, "sabnzbd", env))
    await p.startup()
    try:
        await p.poll("queue")
        params = route.calls.last.request.url.params
        assert params["apikey"] == KEY  # the service gets the real key…
        assert params["mode"] == "queue"  # …merged with the resource's own params

        # …and a failure that quotes the full URL — as httpx errors do — never shows it.
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"cannot connect to {request.url}")

        respx.get("http://sab.lab/api").mock(side_effect=refuse)
        with pytest.raises(ProviderPollError) as ei:
            await p.poll("queue")
    finally:
        await p.shutdown()
    assert KEY not in ei.value.message and MASK in ei.value.message


def test_query_auth_value_must_be_a_secret_reference(config_dir: Path) -> None:
    from hud.config import ConfigManager  # noqa: PLC0415

    (config_dir / "settings.yaml").write_text("apiVersion: hud/v1\nkind: Settings\n")
    (config_dir / "providers").mkdir(exist_ok=True)
    (config_dir / "providers" / "s.yaml").write_text(
        QUERY_DOC.replace('"${secret:sabnzbd_api_key}"', "literalkey123")
    )
    snap = ConfigManager(config_dir).load()  # quarantined, not fatal
    (q,) = snap.quarantined
    assert "must be a ${secret:<name>} reference" in q.summary
