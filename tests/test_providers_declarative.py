# SPDX-License-Identifier: Apache-2.0
"""The §7.1 Home Assistant document works end-to-end from YAML alone (Phase 1 milestone)."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from hud.config import ConfigManager, SecretResolver
from hud.config.schemas import ProviderDocument
from hud.models import State, Unit
from hud.providers import ProviderBuildError, ProviderContext, ProviderPollError
from hud.providers.declarative import DeclarativeProvider, build_declarative
from tests.conftest import FIXTURES

HA_URL = "http://ha.lab:8123"
STATES = json.loads((FIXTURES / "home-assistant" / "states.json").read_text())


def load_doc(config_dir: Path, text: str) -> ProviderDocument:
    (config_dir / "settings.yaml").write_text("apiVersion: hud/v1\nkind: Settings\n")
    (config_dir / "providers").mkdir(exist_ok=True)
    (config_dir / "providers" / "p.yaml").write_text(text)
    snap = ConfigManager(config_dir).load()
    (doc,) = [d.model for d in snap.documents if isinstance(d.model, ProviderDocument)]
    return doc


def make_ctx(config_dir: Path, name: str, env: dict[str, str]) -> ProviderContext:
    secrets = SecretResolver(config_dir, secrets_dir=config_dir / "run-secrets", env=env)
    return ProviderContext.create(name, secrets, env)


@pytest.fixture
def ha_env() -> dict[str, str]:
    return {"HA_BASE_URL": HA_URL, "HUD_SECRET_HOME_ASSISTANT_TOKEN": "tok-abc"}


@pytest.fixture
def ha_provider(config_dir: Path, ha_env: dict[str, str]) -> DeclarativeProvider:
    doc = load_doc(config_dir, (FIXTURES / "providers" / "home-assistant.yaml").read_text())
    p = build_declarative(doc, make_ctx(config_dir, "home-assistant", ha_env))
    assert isinstance(p, DeclarativeProvider)
    return p


@respx.mock
async def test_home_assistant_end_to_end(ha_provider: DeclarativeProvider) -> None:
    route = respx.get(f"{HA_URL}/api/states").mock(return_value=httpx.Response(200, json=STATES))
    await ha_provider.startup()
    try:
        assert [g.name for g in ha_provider.groups()] == ["sensors", "automations"]
        assert ha_provider.group("sensors").interval == 30
        assert ha_provider.group("sensors").jitter == 5
        assert ha_provider.group("automations").interval == 300
        assert ha_provider.kinds == {"sensor", "automation"}

        result = await ha_provider.poll("sensors")
    finally:
        await ha_provider.shutdown()

    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer tok-abc"
    assert sent.headers["Accept"] == "application/json"

    by_uid = {r.uid: r for r in result.resources}
    assert set(by_uid) == {
        "home-assistant:sensor:sensor.living_room_temperature",
        "home-assistant:sensor:sensor.rack_power",
        "home-assistant:sensor:sensor.nas_free_space",
        "home-assistant:sensor:sensor.outdoor_humidity",
        "home-assistant:sensor:sensor.last_boot",
        "home-assistant:sensor:sensor.air_quality_index",
    }
    temp = by_uid["home-assistant:sensor:sensor.living_room_temperature"]
    assert temp.name == "Living Room Temperature"
    assert temp.state is State.UP
    assert temp.attrs == {"device_class": "temperature", "raw_state": 21.5}
    assert temp.links == {"ui": f"{HA_URL}/history?entity_id=sensor.living_room_temperature"}
    assert temp.provider == "home-assistant" and temp.kind == "sensor"
    assert not temp.stale

    assert by_uid["home-assistant:sensor:sensor.outdoor_humidity"].state is State.DOWN
    # No friendly_name → default(entity_id); missing device_class → None, not "Undefined".
    boot = by_uid["home-assistant:sensor:sensor.last_boot"]
    assert boot.name == "sensor.last_boot"
    assert boot.attrs["device_class"] == "timestamp"

    metrics = {m.resource_uid.rsplit(":", 1)[1]: m for m in result.metrics}
    # `when: is_number` drops unavailable/timestamp states; units normalize at the boundary.
    assert set(metrics) == {
        "sensor.living_room_temperature",
        "sensor.rack_power",
        "sensor.nas_free_space",
        "sensor.air_quality_index",
    }
    temp_m = metrics["sensor.living_room_temperature"]
    assert (temp_m.value, temp_m.unit) == (21.5, Unit.CELSIUS)
    power = metrics["sensor.rack_power"]
    assert (power.value, power.unit) == (412.0, Unit.WATTS)
    assert metrics["sensor.nas_free_space"].unit is Unit.BYTES
    assert metrics["sensor.nas_free_space"].value == pytest.approx(1843.2 * 1024**3)
    assert metrics["sensor.air_quality_index"].unit is Unit.NONE  # unknown alias → dimensionless
    assert all(m.name == "value" for m in result.metrics)
    assert result.events == []


@respx.mock
async def test_automations_group(ha_provider: DeclarativeProvider) -> None:
    respx.get(f"{HA_URL}/api/states").mock(return_value=httpx.Response(200, json=STATES))
    await ha_provider.startup()
    try:
        result = await ha_provider.poll("automations")
    finally:
        await ha_provider.shutdown()
    states = {r.name: r.state for r in result.resources}
    assert states == {"Night lights": State.UP, "Vacation mode": State.PAUSED}
    assert result.metrics == []


def test_missing_env_var_fails_the_build(config_dir: Path) -> None:
    doc = load_doc(config_dir, (FIXTURES / "providers" / "home-assistant.yaml").read_text())
    ctx = make_ctx(config_dir, "home-assistant", {"HUD_SECRET_HOME_ASSISTANT_TOKEN": "x"})
    with pytest.raises(ProviderBuildError, match="environment variable 'HA_BASE_URL' is not set"):
        build_declarative(doc, ctx)


def test_missing_secret_fails_the_build(config_dir: Path) -> None:
    doc = load_doc(config_dir, (FIXTURES / "providers" / "home-assistant.yaml").read_text())
    ctx = make_ctx(config_dir, "home-assistant", {"HA_BASE_URL": HA_URL})
    with pytest.raises(ProviderBuildError, match="secret 'home_assistant_token' not found"):
        build_declarative(doc, ctx)


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (httpx.Response(500, text="boom"), "HTTP 500"),
        (httpx.Response(401, text="nope"), "HTTP 401"),
        (
            httpx.Response(200, text="<html>", headers={"content-type": "text/html"}),
            "not JSON",
        ),
    ],
)
@respx.mock
async def test_http_failures_are_poll_errors(
    ha_provider: DeclarativeProvider, response: httpx.Response, match: str
) -> None:
    respx.get(f"{HA_URL}/api/states").mock(return_value=response)
    await ha_provider.startup()
    try:
        with pytest.raises(ProviderPollError, match=match):
            await ha_provider.poll("sensors")
    finally:
        await ha_provider.shutdown()


@respx.mock
async def test_timeout_is_a_poll_error(ha_provider: DeclarativeProvider) -> None:
    respx.get(f"{HA_URL}/api/states").mock(side_effect=httpx.ReadTimeout("slow"))
    await ha_provider.startup()
    try:
        with pytest.raises(ProviderPollError, match=r"timed out after 5\.0s"):
            await ha_provider.poll("sensors")
    finally:
        await ha_provider.shutdown()


@respx.mock
async def test_redirect_off_host_is_not_followed(ha_provider: DeclarativeProvider) -> None:
    respx.get(f"{HA_URL}/api/states").mock(
        return_value=httpx.Response(302, headers={"location": "http://evil.example/"})
    )
    evil = respx.get("http://evil.example/").mock(return_value=httpx.Response(200, json=[]))
    await ha_provider.startup()
    try:
        with pytest.raises(ProviderPollError, match="HTTP 302"):
            await ha_provider.poll("sensors")
    finally:
        await ha_provider.shutdown()
    assert not evil.called


BROKEN_TEMPLATE = """\
apiVersion: hud/v1
kind: Provider
metadata:
  name: demo
spec:
  transport: { base_url: http://demo.lab }
  resources:
    - name: things
      request: { path: /things }
      map:
        uid: "demo:thing:{{ item.name }}"
        kind: thing
        name: "{{ item.name | nosuchfilter }}"
"""


def test_bad_expression_fails_the_build(config_dir: Path) -> None:
    doc = load_doc(config_dir, BROKEN_TEMPLATE)
    with pytest.raises(ProviderBuildError, match="nosuchfilter"):
        build_declarative(doc, make_ctx(config_dir, "demo", {}))


def test_bad_jsonpath_fails_the_build(config_dir: Path) -> None:
    doc = load_doc(
        config_dir,
        BROKEN_TEMPLATE.replace("| nosuchfilter", "").replace(
            "      map:", '      select: "$[?("\n      map:'
        ),
    )
    with pytest.raises(ProviderBuildError, match="invalid JSONPath"):
        build_declarative(doc, make_ctx(config_dir, "demo", {}))


ITEMS_DOC = """\
apiVersion: hud/v1
kind: Provider
metadata:
  name: demo
spec:
  transport: { base_url: http://demo.lab }
  resources:
    - name: things
      request: { path: /things }
      map:
        uid: "demo:thing:{{ item.name }}"
        kind: thing
        name: "{{ item.name }}"
        state: "{{ item.status }}"
"""


@respx.mock
async def test_partial_item_failures_are_skipped_and_state_coerced(config_dir: Path) -> None:
    respx.get("http://demo.lab/things").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "ok", "status": "UP"},
                {"name": "weird", "status": "sideways"},
                {"name": "a" * 64, "status": "up"},  # hash-shaped native id: refused
                {"name": "", "status": "up"},  # empty native id: refused
                {"name": "ok", "status": "down"},  # duplicate uid: refused
            ],
        )
    )
    p = build_declarative(load_doc(config_dir, ITEMS_DOC), make_ctx(config_dir, "demo", {}))
    await p.startup()
    try:
        result = await p.poll("things")
    finally:
        await p.shutdown()
    assert {r.name: r.state for r in result.resources} == {"ok": State.UP, "weird": State.UNKNOWN}


@respx.mock
async def test_all_items_failing_fails_the_poll(config_dir: Path) -> None:
    respx.get("http://demo.lab/things").mock(
        return_value=httpx.Response(200, json=[{"nope": 1}, {"nope": 2}])
    )
    p = build_declarative(load_doc(config_dir, ITEMS_DOC), make_ctx(config_dir, "demo", {}))
    await p.startup()
    try:
        with pytest.raises(ProviderPollError, match="all 2 items failed to map"):
            await p.poll("things")
        # An empty response is not an error: nothing there is a valid answer.
        respx.get("http://demo.lab/things").mock(return_value=httpx.Response(200, json=[]))
        assert (await p.poll("things")).resources == []
    finally:
        await p.shutdown()


PAGED_DOC = """\
apiVersion: hud/v1
kind: Provider
metadata:
  name: demo
spec:
  transport: { base_url: http://demo.lab }
  resources:
    - name: things
      request: { path: /things, params: { sort: name } }
      select: "$.records"
      paginate: { style: PAGESTYLE }
      map:
        uid: "demo:thing:{{ item.name }}"
        kind: thing
        name: "{{ item.name }}"
"""


@respx.mock
async def test_page_pagination_stops_on_empty_page(config_dir: Path) -> None:
    pages = {"1": ["a", "b"], "2": ["c"], "3": []}

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params["page"]
        assert request.url.params["sort"] == "name"
        return httpx.Response(200, json={"records": [{"name": n} for n in pages[page]]})

    route = respx.get("http://demo.lab/things").mock(side_effect=handler)
    text = PAGED_DOC.replace("PAGESTYLE", "page, param: page")
    p = build_declarative(load_doc(config_dir, text), make_ctx(config_dir, "demo", {}))
    await p.startup()
    try:
        result = await p.poll("things")
    finally:
        await p.shutdown()
    assert [r.name for r in result.resources] == ["a", "b", "c"]
    assert route.call_count == 3


@respx.mock
async def test_offset_pagination(config_dir: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        assert request.url.params["limit"] == "2"
        names = ["a", "b", "c", "d", "e"][offset : offset + 2]
        return httpx.Response(200, json={"records": [{"name": n} for n in names]})

    route = respx.get("http://demo.lab/things").mock(side_effect=handler)
    text = PAGED_DOC.replace("PAGESTYLE", "offset, param: offset, size_param: limit, size: 2")
    p = build_declarative(load_doc(config_dir, text), make_ctx(config_dir, "demo", {}))
    await p.startup()
    try:
        result = await p.poll("things")
    finally:
        await p.shutdown()
    assert [r.name for r in result.resources] == ["a", "b", "c", "d", "e"]
    assert route.call_count == 4  # 2,2,1,0


@respx.mock
async def test_cursor_pagination(config_dir: Path) -> None:
    chain = {None: ("x1", ["a"]), "x1": ("x2", ["b"]), "x2": (None, ["c"])}

    def handler(request: httpx.Request) -> httpx.Response:
        nxt, names = chain[request.url.params.get("cursor")]
        return httpx.Response(200, json={"records": [{"name": n} for n in names], "next": nxt})

    route = respx.get("http://demo.lab/things").mock(side_effect=handler)
    text = PAGED_DOC.replace("PAGESTYLE", 'cursor, param: cursor, cursor_path: "$.next"')
    p = build_declarative(load_doc(config_dir, text), make_ctx(config_dir, "demo", {}))
    await p.startup()
    try:
        result = await p.poll("things")
    finally:
        await p.shutdown()
    assert [r.name for r in result.resources] == ["a", "b", "c"]
    assert route.call_count == 3


SINGLETON_DOC = """\
apiVersion: hud/v1
kind: Provider
metadata: { name: demo }
spec:
  transport: { base_url: http://demo.lab }
  defaults: { interval: 1h, jitter: 0s }
  resources:
    - name: service
      request: { path: /status }
      map:
        uid: "demo:service:main"
        kind: service
        name: "{{ item.appName }}"
        state: up
"""


@respx.mock
async def test_singleton_literal_uid_maps_one_resource(config_dir: Path) -> None:
    """A single-object endpoint keys on a literal: there is no item field to use, and a
    constant is the most stable id available (invariant 8)."""
    respx.get("http://demo.lab/status").mock(
        return_value=httpx.Response(200, json={"appName": "Demo", "version": "1.2.3"})
    )
    p = build_declarative(load_doc(config_dir, SINGLETON_DOC), make_ctx(config_dir, "demo", {}))
    await p.startup()
    try:
        result = await p.poll("service")
    finally:
        await p.shutdown()
    (resource,) = result.resources
    assert resource.uid == "demo:service:main" and resource.name == "Demo"


@respx.mock
async def test_literal_uid_over_a_list_collapses_and_is_refused(config_dir: Path) -> None:
    """The one risk a constant native id carries: every item collapsing onto one uid. The
    duplicate-uid guard catches it per poll, which is why the schema may allow literals."""
    respx.get("http://demo.lab/status").mock(
        return_value=httpx.Response(200, json=[{"appName": "One"}, {"appName": "Two"}])
    )
    p = build_declarative(load_doc(config_dir, SINGLETON_DOC), make_ctx(config_dir, "demo", {}))
    await p.startup()
    try:
        result = await p.poll("service")
        # The first item maps; the second is refused rather than silently overwriting it.
        assert [r.name for r in result.resources] == ["One"]
    finally:
        await p.shutdown()
