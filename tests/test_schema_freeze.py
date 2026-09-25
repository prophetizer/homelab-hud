# SPDX-License-Identifier: Apache-2.0
"""The ``dashboard/v1`` freeze guard (PLAN.md §11.3, R10).

``schema/dashboard-v1.json`` is the published contract: the release asset editors point at,
and the machine-readable record of what was frozen. These tests fail when the provider or
board schema drifts from it, so a breaking change to strangers' configs cannot land by
accident — only by regenerating the file on purpose, which shows up in review as a diff of
the contract rather than a diff of a Pydantic model.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from hud.config.schema_export import SCHEMA_PATH, build_schema, render
from hud.config.schemas import KIND_SCHEMAS

REGENERATE = "regenerate deliberately with: uv run python -m hud.config.schema_export --write"


@pytest.fixture(scope="module")
def published() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_published_schema_matches_the_models(published: dict[str, Any]) -> None:
    fresh = build_schema()
    if fresh != published:
        frozen = [
            k
            for k in published.get("x-frozen", [])
            if fresh["$defs"].get(k) != published["$defs"].get(k)
        ]
        detail = f"FROZEN kinds changed: {', '.join(frozen)}. " if frozen else ""
        pytest.fail(
            f"{SCHEMA_PATH.name} is out of date with the config models. {detail}"
            f"A change to a frozen kind is a dashboard/v2, not a v1 release. {REGENERATE}"
        )


def test_published_file_is_byte_identical_to_the_renderer(published: dict[str, Any]) -> None:
    # Guards formatting too, so the file stays reviewable as a diff.
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render(published), REGENERATE


def test_every_ref_resolves(published: dict[str, Any]) -> None:
    """A dangling ``$ref`` makes the published schema useless to an editor while still
    round-tripping through a golden-file comparison — so it is checked separately."""
    refs = set(re.findall(r'"#/\$defs/([A-Za-z0-9_.-]+)"', json.dumps(published)))
    assert refs, "no $refs found; the exporter changed shape"
    assert refs <= set(published["$defs"]), f"dangling: {sorted(refs - set(published['$defs']))}"


def test_every_document_kind_is_published(published: dict[str, Any]) -> None:
    # A new `kind` the loader accepts but the schema omits would be invisible to editors.
    assert set(KIND_SCHEMAS) <= set(published["$defs"])
    assert {ref["$ref"].removeprefix("#/$defs/") for ref in published["oneOf"]} == set(KIND_SCHEMAS)


def test_frozen_kinds_are_the_ones_the_plan_names(published: dict[str, Any]) -> None:
    # Settings and RBAC are published for completion but may still gain optional keys.
    assert published["x-frozen"] == ["Provider", "Board"]


def test_bundled_templates_are_the_freeze_evidence() -> None:
    """The freeze's precondition: five real services exercised by hand-written YAML."""
    templates = sorted(
        p.stem for p in (Path(__file__).parent.parent / "templates" / "providers").glob("*.yaml")
    )
    assert templates == ["home-assistant", "plex", "radarr", "sonarr"]
    # Plus the two packaged plugins — docker and sonarr — for five distinct services.


# --------------------------------------------------------------- the schema as a schema

CONFIG_DOCS = sorted((Path(__file__).parent.parent / "templates" / "providers").glob("*.yaml"))


def _validator(published: dict[str, Any]) -> Any:
    import jsonschema  # noqa: PLC0415 — dev-only dependency, never imported at runtime

    jsonschema.Draft202012Validator.check_schema(published)
    return jsonschema.Draft202012Validator(published)


def test_published_schema_is_a_valid_json_schema(published: dict[str, Any]) -> None:
    _validator(published)  # raises SchemaError if the export is not well-formed


@pytest.mark.parametrize("template", CONFIG_DOCS, ids=lambda p: p.stem)
def test_bundled_templates_validate_against_the_published_schema(
    published: dict[str, Any], template: Path
) -> None:
    """The published artifact must accept the documents HUD itself ships. A golden-file
    comparison alone would happily freeze a schema that accepts nothing."""
    from hud.config.loader import parse_yaml  # noqa: PLC0415

    document = json.loads(json.dumps(parse_yaml(template.read_text(), template)))
    errors = sorted(_validator(published).iter_errors(document), key=lambda e: list(e.path))
    assert not errors, f"{template.name}: " + "; ".join(
        f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors[:5]
    )


def test_published_schema_refuses_a_broken_document(published: dict[str, Any]) -> None:
    # The converse: a schema that accepts everything would also pass the test above.
    broken = {
        "apiVersion": "hud/v1",
        "kind": "Board",
        "metadata": {"name": "Not A Slug"},  # boards are lowercase; the pattern must bite
        "spec": {"widgets": [{"id": "x", "type": "static"}]},  # and grid is required
    }
    assert list(_validator(published).iter_errors(broken)), "schema accepts an invalid board"
