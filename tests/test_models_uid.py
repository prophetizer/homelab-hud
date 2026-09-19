# SPDX-License-Identifier: Apache-2.0
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hud.models import Resource, State, make_uid

NOW = datetime(2026, 9, 18, tzinfo=UTC)


def _resource(uid: str, provider: str = "docker", kind: str = "container") -> Resource:
    return Resource(uid=uid, provider=provider, kind=kind, name="x", state=State.UP, fetched_at=NOW)


def test_make_uid_canonical_form() -> None:
    assert make_uid("docker", "container", "sonarr") == "docker:container:sonarr"


def test_uid_survives_recreate_when_built_from_name() -> None:
    before = _resource(make_uid("docker", "container", "sonarr"))
    after = _resource(make_uid("docker", "container", "sonarr"))
    assert before.uid == after.uid
    assert before.native_id == "sonarr"


@pytest.mark.parametrize(
    "volatile",
    [
        "3f2a9c1b8e7d",  # docker short id
        "a" * 64,  # docker long id
        "sha256:" + "b" * 64,  # image digest
    ],
)
def test_volatile_native_id_rejected(volatile: str) -> None:
    with pytest.raises(ValueError, match="content hash / container id"):
        make_uid("docker", "container", volatile)
    with pytest.raises(ValidationError, match="content hash / container id"):
        _resource(f"docker:container:{volatile}")


def test_uuid_native_id_allowed() -> None:
    # Many UUIDs are stable (HA entity registry ids, Sonarr series ids); do not reject them.
    uid = make_uid("home-assistant", "sensor", "8f1c2a4e-9b3d-4c6a-a1e2-0f9d8c7b6a5e")
    assert _resource(uid, provider="home-assistant", kind="sensor").uid == uid


def test_uid_must_match_provider_and_kind() -> None:
    with pytest.raises(ValidationError, match="must start with 'docker:container:'"):
        _resource("sonarr:container:foo")
    with pytest.raises(ValidationError, match="must start with"):
        _resource("docker:host:foo")


@pytest.mark.parametrize("bad", ["", " ", "docker:container:", "docker:container: x"])
def test_empty_or_padded_native_id_rejected(bad: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        if bad.startswith("docker:"):
            _resource(bad)
        else:
            make_uid("docker", "container", bad)


@pytest.mark.parametrize("bad_component", ["", "my provider", "a:b"])
def test_provider_and_kind_components_validated(bad_component: str) -> None:
    with pytest.raises(ValueError, match="no whitespace or ':'"):
        make_uid(bad_component, "container", "x")
    with pytest.raises(ValueError, match="no whitespace or ':'"):
        make_uid("docker", bad_component, "x")


def test_resource_is_frozen_and_strict() -> None:
    r = _resource("docker:container:sonarr")
    with pytest.raises(ValidationError):
        r.name = "y"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Resource(
            uid="docker:container:sonarr",
            provider="docker",
            kind="container",
            name="x",
            state=State.UP,
            fetched_at=NOW,
            unexpected="field",  # type: ignore[call-arg]
        )
