# SPDX-License-Identifier: Apache-2.0
"""Expression sandbox (R5) and select semantics."""

import pytest

from hud.providers.declarative.expr import Expr, ExprError, new_environment
from hud.providers.declarative.select import SelectError, Selector

ENV = new_environment()
ITEM = {"entity_id": "sensor.t", "state": "21.5", "attributes": {"unit_of_measurement": "°C"}}


def render(src: str) -> object:
    return Expr(ENV, src).render(item=ITEM)


def test_native_typed_results() -> None:
    assert render("{{ item.state | float }}") == 21.5
    assert render("{{ item.state | float > 20 }}") is True
    assert render("x:{{ item.entity_id }}") == "x:sensor.t"
    assert render("{{ item.attributes.friendly_name | default(item.entity_id) }}") == "sensor.t"
    assert render("{{ item.attributes.missing.deeper }}") is None
    assert Expr(ENV, "plain").render(item=ITEM) == "plain"
    assert Expr(ENV, "plain").is_constant


def test_string_and_bool_coercion() -> None:
    assert Expr(ENV, "{{ item.state }}").render_str(item=ITEM) == "21.5"
    assert Expr(ENV, "{{ item.nope }}").render_str(item=ITEM) == ""
    assert Expr(ENV, "{{ item.state | is_number }}").render_bool(item=ITEM) is True
    assert Expr(ENV, "{{ 'unavailable' | is_number }}").render_bool(item=ITEM) is False
    assert Expr(ENV, "false").render_bool(item=ITEM) is False
    assert Expr(ENV, "{{ item.entity_id | regex_search('^sensor') }}").render_bool(item=ITEM)


@pytest.mark.parametrize(
    "src",
    [
        "{{ ''.__class__.__mro__ }}",
        "{{ item.__class__ }}",
        "{{ lipsum.__globals__ }}",
    ],
)
def test_dunder_access_yields_nothing(src: str) -> None:
    assert render(src) is None


@pytest.mark.parametrize(
    ("src", "match"),
    [
        ("{{ item.update({'a': 1}) }}", "unsafe"),
        ("{% for x in range(10**9) %}{% endfor %}", "Range too big"),
        ("{{ 1 / 0 }}", "ZeroDivisionError"),
        ("{{ item.state | float | nosuch }}", "nosuch"),
    ],
)
def test_unsafe_or_broken_expressions_raise(src: str, match: str) -> None:
    with pytest.raises(ExprError, match=match):
        render(src)


def test_no_include_or_import() -> None:
    with pytest.raises(ExprError):
        render("{% include 'x' %}")
    with pytest.raises(ExprError):
        render("{% import 'x' as y %}{{ y }}")


def test_select_semantics() -> None:
    data = [{"entity_id": "sensor.a"}, {"entity_id": "light.b"}]
    assert Selector("$").items(data) == data  # single list match → its elements
    assert Selector("$[*]").items(data) == data
    assert Selector("$[?(@.entity_id =~ '^sensor\\\\.')]").items(data) == [data[0]]
    assert Selector("$.records").items({"records": data}) == data
    assert Selector("$").items({"a": 1}) == [{"a": 1}]  # single dict → one item
    assert Selector("$.next").first({"next": "c1"}) == "c1"
    assert Selector("$.next").first({}) is None
    with pytest.raises(SelectError, match="invalid JSONPath"):
        Selector("$[?(")
