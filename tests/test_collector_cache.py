# SPDX-License-Identifier: Apache-2.0
"""LiveCache: last-known-good on failure, stale flag, transitions → events, removals."""

from datetime import UTC, datetime, timedelta

from hud.collector import LiveCache, ResourceFilter, normalize
from hud.models import Event, Metric, Resource, Severity, State, Unit
from hud.providers import PollResult

T0 = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def res(uid: str, state: State = State.UP, at: datetime = T0, **attrs: object) -> Resource:
    provider, kind, _ = uid.split(":", 2)
    return Resource(
        uid=uid,
        provider=provider,
        kind=kind,
        name=uid.rsplit(":", 1)[1],
        state=state,
        attrs=dict(attrs),
        fetched_at=at,
    )


def metric(uid: str, name: str, value: float, at: datetime = T0) -> Metric:
    return Metric(resource_uid=uid, name=name, value=value, unit=Unit.COUNT, ts=at)


def event(uid: str, type_: str) -> Event:
    return Event(resource_uid=uid, type=type_, severity=Severity.INFO, message="m", ts=T0)


def test_apply_then_query() -> None:
    c = LiveCache()
    c.register_provider("ha", {"category": "automation"})
    events = c.apply(
        "ha",
        "sensors",
        [res("ha:sensor:a"), res("ha:sensor:b", State.DOWN)],
        [metric("ha:sensor:a", "value", 1.0)],
    )
    assert events == []  # first sight is not a transition
    assert len(c) == 2
    assert c.resource("ha:sensor:a") is not None
    assert c.metric("ha:sensor:a", "value") is not None
    assert c.metric("ha:sensor:a", "nope") is None
    assert [r.uid for r in c.resources(ResourceFilter(state=[State.DOWN]))] == ["ha:sensor:b"]
    assert [r.uid for r in c.resources(ResourceFilter(kind=["sensor"], provider=["ha"]))] == [
        "ha:sensor:a",
        "ha:sensor:b",
    ]
    assert c.resources(ResourceFilter(labels={"category": "automation"})) != []
    assert c.resources(ResourceFilter(labels={"category": "media"})) == []
    assert c.provider_resource_count("ha") == 2
    gen = c.generation
    c.apply("ha", "sensors", [res("ha:sensor:a"), res("ha:sensor:b", State.DOWN)], [])
    assert c.generation == gen + 1


def test_state_transition_emits_event_and_removal_drops_metrics() -> None:
    c = LiveCache()
    c.apply("ha", "s", [res("ha:sensor:a"), res("ha:sensor:b")], [metric("ha:sensor:b", "v", 1)])
    t1 = T0 + timedelta(seconds=30)
    events = c.apply("ha", "s", [res("ha:sensor:a", State.DOWN, at=t1)], [])
    assert [(e.type, e.resource_uid, e.severity) for e in events] == [
        ("state_change", "ha:sensor:a", Severity.ERROR),
        ("removed", "ha:sensor:b", Severity.INFO),
    ]
    assert events[0].message == "a: up → down"
    assert events[0].ts == t1
    assert c.resource("ha:sensor:b") is None
    assert c.metric("ha:sensor:b", "v") is None
    assert c.metrics_for("ha:sensor:b") == []
    # Ring buffer holds them, most recent first, filterable by uid.
    assert [e.type for e in c.events()] == ["removed", "state_change"]
    assert [e.type for e in c.events(uid="ha:sensor:a")] == ["state_change"]
    assert len(c.events(limit=1)) == 1


def test_recovery_severity_is_info() -> None:
    c = LiveCache()
    c.apply("ha", "s", [res("ha:sensor:a", State.DOWN)], [])
    (e,) = c.apply("ha", "s", [res("ha:sensor:a", State.UP)], [])
    assert e.severity is Severity.INFO


def test_mark_stale_keeps_last_known_good_until_next_success() -> None:
    c = LiveCache()
    c.apply("ha", "s", [res("ha:sensor:a")], [metric("ha:sensor:a", "v", 7)])
    c.mark_stale("ha", "s", "HTTP 500")
    r = c.resource("ha:sensor:a")
    assert r is not None and r.stale and r.state is State.UP
    assert c.metric("ha:sensor:a", "v") is not None  # last value still served
    assert c.group_error("ha", "s") == "HTTP 500"
    c.apply("ha", "s", [res("ha:sensor:a", at=T0 + timedelta(seconds=60))], [])
    r = c.resource("ha:sensor:a")
    assert r is not None and not r.stale
    assert c.group_error("ha", "s") is None


def test_groups_are_independent() -> None:
    c = LiveCache()
    c.apply("ha", "sensors", [res("ha:sensor:a")], [])
    c.apply("ha", "automations", [res("ha:automation:x")], [])
    # A sensors poll that no longer lists 'a' must not touch automations.
    events = c.apply("ha", "sensors", [], [])
    assert [e.resource_uid for e in events] == ["ha:sensor:a"]
    assert c.resource("ha:automation:x") is not None
    c.mark_stale("ha", "sensors", "x")
    x = c.resource("ha:automation:x")
    assert x is not None and not x.stale


def test_remove_provider_drops_everything_it_owned() -> None:
    c = LiveCache()
    c.register_provider("ha", {})
    c.apply("ha", "s", [res("ha:sensor:a")], [metric("ha:sensor:a", "v", 1)])
    c.apply("dk", "c", [res("dk:container:web")], [])
    events = c.remove_provider("ha")
    assert [e.resource_uid for e in events] == ["ha:sensor:a"]
    assert events[0].message == "a: provider removed"
    assert len(c) == 1 and c.resource("dk:container:web") is not None
    assert c.provider_resource_count("ha") == 0


def test_metric_for_unknown_resource_is_ignored() -> None:
    c = LiveCache()
    c.apply("ha", "s", [res("ha:sensor:a")], [metric("ha:sensor:zzz", "v", 1)])
    assert c.metrics_for("ha:sensor:zzz") == []


def test_incoming_stale_flag_is_reset_on_success() -> None:
    c = LiveCache()
    stale = res("ha:sensor:a").model_copy(update={"stale": True})
    c.apply("ha", "s", [stale], [])
    r = c.resource("ha:sensor:a")
    assert r is not None and not r.stale


def test_provider_events_pass_through_in_order() -> None:
    c = LiveCache()
    own = event("ha:sensor:a", "import")
    c.apply("ha", "s", [res("ha:sensor:a")], [], [own])
    events = c.apply("ha", "s", [res("ha:sensor:a", State.DOWN)], [], [own])
    assert [e.type for e in events] == ["state_change", "import"]


def test_normalize_drops_foreign_and_duplicate_objects() -> None:
    result = PollResult(
        resources=[res("ha:sensor:a"), res("dk:container:x"), res("ha:sensor:a")],
        metrics=[metric("ha:sensor:a", "v", 1), metric("dk:container:x", "v", 1)],
        events=[event("dk:container:x", "t")],
    )
    n = normalize("ha", result)
    assert [r.uid for r in n.resources] == ["ha:sensor:a"]
    assert [m.resource_uid for m in n.metrics] == ["ha:sensor:a"]
    assert n.events == []
    assert n.dropped == 4
