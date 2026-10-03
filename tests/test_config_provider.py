# SPDX-License-Identifier: Apache-2.0
"""``kind: Provider`` validation: the §7.1 example loads; the invariants are enforced."""

from pathlib import Path

import pytest

from hud.config import ConfigManager
from hud.config.schemas import ProviderDocument
from hud.config.schemas.provider import AuthBearer, check_uid_template
from tests.conftest import FIXTURES

SETTINGS = "apiVersion: hud/v1\nkind: Settings\n"

MINIMAL = """\
apiVersion: hud/v1
kind: Provider
metadata:
  name: demo
spec:
  transport:
    base_url: http://demo.lab
  resources:
    - name: things
      request: { path: /api/things }
      map:
        uid: "demo:thing:{{ item.name }}"
        kind: thing
        name: "{{ item.name }}"
"""


def _write(config_dir: Path, name: str, text: str) -> Path:
    (config_dir / "providers").mkdir(exist_ok=True)
    p = config_dir / "providers" / name
    p.write_text(text)
    return p


def _load_provider(manager: ConfigManager, config_dir: Path, text: str) -> ProviderDocument:
    (config_dir / "settings.yaml").write_text(SETTINGS)
    _write(config_dir, "p.yaml", text)
    snap = manager.load()
    (doc,) = [d for d in snap.documents if d.model.kind == "Provider"]
    assert isinstance(doc.model, ProviderDocument)
    return doc.model


def _issues(manager: ConfigManager, config_dir: Path, text: str) -> list[str]:
    (config_dir / "settings.yaml").write_text(SETTINGS)
    _write(config_dir, "p.yaml", text)
    # An invalid Provider is quarantined, not fatal (invariant 6): the load succeeds, the
    # file is refused and not loaded, and its issues are reported with file:line.
    snap = manager.load()
    assert not [d for d in snap.documents if isinstance(d.model, ProviderDocument)], (
        "invalid file loaded"
    )
    (quarantined,) = snap.quarantined
    assert quarantined.kind == "Provider" and not quarantined.serving_last_good
    return [str(i) for i in quarantined.issues]


def test_plan_example_loads(manager: ConfigManager, config_dir: Path) -> None:
    text = (FIXTURES / "providers" / "home-assistant.yaml").read_text()
    doc = _load_provider(manager, config_dir, text)
    assert doc.metadata.name == "home-assistant"
    assert doc.metadata.labels == {"category": "automation"}
    t = doc.spec.transport
    assert t.base_url == "${HA_BASE_URL}"
    assert isinstance(t.auth, AuthBearer)
    assert t.auth.token == "${secret:home_assistant_token}"
    assert t.timeout_seconds == 5
    assert doc.spec.defaults.interval_seconds == 30
    assert doc.spec.defaults.jitter_seconds == 5
    sensors, automations = doc.spec.resources
    assert sensors.select.startswith("$[?(")
    assert sensors.map.kind == "sensor"
    assert sensors.metrics[0].unit_from is not None
    assert automations.interval == "5m"
    (action,) = doc.spec.actions
    assert action.verb == "toggle"
    assert action.request.method == "POST"
    assert manager.snapshot.warnings == ()


def test_minimal_defaults(manager: ConfigManager, config_dir: Path) -> None:
    doc = _load_provider(manager, config_dir, MINIMAL)
    assert doc.spec.transport.auth.type == "none"
    assert doc.spec.transport.timeout_seconds == 10
    assert doc.spec.resources[0].request.method == "GET"
    assert doc.spec.resources[0].select == "$"
    assert doc.spec.resources[0].map.state == "unknown"


def test_literal_bearer_token_refused(manager: ConfigManager, config_dir: Path) -> None:
    text = MINIMAL.replace(
        "    base_url: http://demo.lab\n",
        "    base_url: http://demo.lab\n    auth: { type: bearer, token: hunter2 }\n",
    )
    (msg,) = _issues(manager, config_dir, text)
    # The literal-secret scanner runs before schema validation and points at the value.
    assert "providers/p.yaml:8:34" in msg
    assert "'spec.transport.auth.token' looks like a literal secret" in msg


def test_api_key_must_be_secret_ref(manager: ConfigManager, config_dir: Path) -> None:
    text = MINIMAL.replace(
        "    base_url: http://demo.lab\n",
        "    base_url: http://demo.lab\n    auth: { type: api_key, key: notasecretref }\n",
    )
    (msg,) = _issues(manager, config_dir, text)
    assert "spec.transport.auth.api_key.key" in msg
    assert "${secret:<name>}" in msg


def test_uid_must_carry_provider_and_kind_prefix(manager: ConfigManager, config_dir: Path) -> None:
    text = MINIMAL.replace('uid: "demo:thing:', 'uid: "other:thing:')
    (msg,) = _issues(manager, config_dir, text)
    assert "spec.resources.0.map.uid" in msg
    assert "must start with 'demo:thing:'" in msg


def test_uid_from_volatile_field_refused(manager: ConfigManager, config_dir: Path) -> None:
    text = MINIMAL.replace('uid: "demo:thing:{{ item.name }}"', 'uid: "demo:thing:{{ item.Id }}"')
    (msg,) = _issues(manager, config_dir, text)
    assert "item.Id, which is volatile" in msg
    assert "forks" in msg


@pytest.mark.parametrize(
    ("template", "problem"),
    [
        ("demo:thing:{{ item.name }}", None),
        ("demo:thing:{{ item.attributes.friendly_name | default(item.entity_id) }}", None),
        ("demo:thing:{{ item['host.name'] }}", None),
        ("demo:thing:{{ item.id }}", "volatile"),
        ("demo:thing:{{ item.ImageID }}", "volatile"),
        ("demo:thing:{{ item.meta.digest }}", "volatile"),
        # Spelling the read as a call does not hide which field it reads.
        ("demo:thing:{{ item.get('id') }}", "volatile"),
        ('demo:thing:{{ item.meta.get("hash") }}', "volatile"),
        ("demo:thing:{{ item.get('name') }}", None),
        # A singleton resource (one service, one status endpoint) keys on a literal.
        ("demo:thing:main", None),
        ("demo:thing:node-1/root", None),
        ("demo:thing:has space", "literal native id"),
        ("demo:thing:-leading-dash", "literal native id"),
        ("demo:thing:{{ 1 + 1 }}", "references no item field"),
        ("demo:thing:", "empty"),
        ("demo:other:{{ item.name }}", "must start with"),
    ],
)
def test_check_uid_template(template: str, problem: str | None) -> None:
    result = check_uid_template("demo", "thing", template)
    if problem is None:
        assert result is None
    else:
        assert result is not None and problem in result


def test_a_stable_reason_admits_a_volatile_named_field_and_must_say_something() -> None:
    reason = "the id is the name the user chose, fixed once created"
    assert check_uid_template("demo", "thing", "demo:thing:{{ item.id }}", reason) is None
    assert check_uid_template("demo", "thing", "demo:thing:{{ item.id }}") is not None


def test_uid_stable_reason_in_a_provider_file(manager: ConfigManager, config_dir: Path) -> None:
    named = MINIMAL.replace('uid: "demo:thing:{{ item.name }}"', 'uid: "demo:thing:{{ item.id }}"')
    assert any("uid_stable_reason" in m for m in _issues(manager, config_dir, named))
    short = named.replace(
        'uid: "demo:thing:{{ item.id }}"',
        'uid: "demo:thing:{{ item.id }}"\n        uid_stable_reason: "trust me"',
    )
    assert any("uid_stable_reason" in m for m in _issues(manager, config_dir, short))


def test_kind_must_be_literal(manager: ConfigManager, config_dir: Path) -> None:
    text = MINIMAL.replace("kind: thing\n", 'kind: "{{ item.kind }}"\n')
    msgs = _issues(manager, config_dir, text)
    assert any("kind must be a literal identifier" in m for m in msgs)


def test_absolute_request_path_refused(manager: ConfigManager, config_dir: Path) -> None:
    text = MINIMAL.replace("path: /api/things", "path: https://evil.example/x")
    (msg,) = _issues(manager, config_dir, text)
    assert "relative to transport.base_url" in msg


def test_metric_needs_exactly_one_unit(manager: ConfigManager, config_dir: Path) -> None:
    text = MINIMAL + '      metrics:\n        - { name: v, value: "{{ item.v }}" }\n'
    (msg,) = _issues(manager, config_dir, text)
    assert "exactly one of unit or unit_from" in msg


def test_duplicate_resource_names(manager: ConfigManager, config_dir: Path) -> None:
    dup = MINIMAL + MINIMAL[MINIMAL.index("    - name: things") :]
    (msg,) = _issues(manager, config_dir, dup)
    assert "duplicate resource names: things" in msg


def test_unknown_key_inside_resource_item_warns(manager: ConfigManager, config_dir: Path) -> None:
    text = MINIMAL + "      bogus: 1\n"
    doc = _load_provider(manager, config_dir, text)
    assert doc.metadata.name == "demo"
    (warning,) = manager.snapshot.warnings
    assert "unknown key 'spec.resources.0.bogus'" in warning.message
    assert warning.line == 15


def test_bad_duration_names_line(manager: ConfigManager, config_dir: Path) -> None:
    text = MINIMAL.replace("      request:", "      interval: 5x\n      request:")
    (msg,) = _issues(manager, config_dir, text)
    assert "providers/p.yaml:10" in msg
    assert "invalid duration '5x'" in msg
