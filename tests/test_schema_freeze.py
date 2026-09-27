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

from hud.config.schema_export import SCHEMA_PATH, build_schema, compare, render
from hud.config.schemas import KIND_SCHEMAS

REGENERATE = "regenerate deliberately with: uv run python -m hud.config.schema_export --write"


@pytest.fixture(scope="module")
def published() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_published_schema_matches_the_models(published: dict[str, Any]) -> None:
    """Fails on ANY drift, so no change lands unreviewed — and says which kind it is:
    additive changes are allowed in a v1 minor (§11.3a); breaking ones are dashboard/v2."""
    fresh = build_schema()
    if fresh != published:
        changes = compare(published, fresh)
        verdict = (
            "BREAKING: this is dashboard/v2, which needs a migrator (R10), not a regenerate."
            if any(c.startswith("BREAKING") for c in changes)
            else "Additive only: v1-compatible (§11.3a) once regenerated deliberately."
        )
        pytest.fail(
            f"{SCHEMA_PATH.name} is out of date with the config models.\n  "
            + "\n  ".join(changes)
            + f"\n{verdict} {REGENERATE}"
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
    """The freeze rests on these templates having exercised the spec (with the docker and
    sonarr plugins, five services). They must stay bundled; more may join (§11.3a)."""
    folder = Path(__file__).parent.parent / "templates" / "providers"
    bundled = {p.stem for p in folder.glob("*.yaml")}
    assert {"home-assistant", "plex", "radarr", "sonarr"} <= bundled


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


def test_compare_classifies_additive_and_breaking() -> None:
    base = {"$defs": {"Select": {"type": "object", "properties": {"kind": {"type": "string"}}}}}
    added = {
        "$defs": {
            "Select": {
                "type": "object",
                "properties": {"kind": {"type": "string"}, "attrs": {"type": "object"}},
            }
        }
    }
    assert compare(base, added) == ["additive: Select.attrs added (optional)"]
    assert compare(added, base) == ["BREAKING: Select.attrs removed"]
    required = {"$defs": {"Select": {**added["$defs"]["Select"], "required": ["attrs"]}}}
    assert compare(base, required) == ["BREAKING: Select.attrs added as required"]
    retyped = {"$defs": {"Select": {"type": "object", "properties": {"kind": {"type": "integer"}}}}}
    assert compare(base, retyped) == ["BREAKING: Select.kind changed"]
    assert compare(base, {"$defs": {**base["$defs"], "New": {}}}) == [
        "additive: new definition New"
    ]


def test_compare_does_not_call_a_parents_embedded_default_breaking() -> None:
    """Found adding Select.attrs: ListSource.select's default embeds Select's defaults, so it
    gained `"attrs": null`. That is the new field's own default — nothing changes meaning."""

    def doc(default: dict[str, Any]) -> dict[str, Any]:
        prop = {"$ref": "#/$defs/Select", "default": default}
        return {"$defs": {"ListSource": {"properties": {"select": prop}}}}

    before = doc({"kind": None, "label": None})
    after = doc({"label": None, "kind": None, "attrs": None})
    assert compare(before, after) == [
        "additive: ListSource.select default gains a new optional field at its default"
    ]
    # A default that changes a value is still breaking.
    moved = doc({"kind": "container", "label": None})
    assert compare(before, moved) == ["BREAKING: ListSource.select changed"]


def test_compare_treats_a_widened_union_as_additive() -> None:
    """Found adding the query auth type: Transport.auth's oneOf and discriminator mapping
    gained a member. The union accepts strictly more, which §11.3a allows in v1."""

    def doc(members: list[str]) -> dict[str, Any]:
        auth = {
            "oneOf": [{"$ref": f"#/$defs/{m}"} for m in members],
            "discriminator": {
                "propertyName": "type",
                "mapping": {m.lower(): f"#/$defs/{m}" for m in members},
            },
        }
        return {"$defs": {"Transport": {"properties": {"auth": auth}}}}

    assert compare(doc(["AuthNone", "AuthBasic"]), doc(["AuthNone", "AuthBasic", "AuthQuery"])) == [
        "additive: Transport.auth accepts AuthQuery as well"
    ]
    # Dropping a member refuses configs that validated before.
    assert compare(doc(["AuthNone", "AuthBasic"]), doc(["AuthNone"])) == [
        "BREAKING: Transport.auth changed"
    ]


def test_compare_treats_a_widened_union_inside_a_list_as_additive() -> None:
    """Found adding the bars widget type: BoardSpec.widgets is an array whose items are the
    widget union, so the new member sits one level down, under ``items``."""

    def doc(members: list[str], extra: dict[str, Any] | None = None) -> dict[str, Any]:
        items = {"oneOf": [{"$ref": f"#/$defs/{m}"} for m in members], **(extra or {})}
        widgets = {"items": items, "title": "Widgets", "type": "array"}
        return {"$defs": {"BoardSpec": {"properties": {"widgets": widgets}}}}

    before = doc(["ListWidget", "UnsupportedWidget"])
    assert compare(before, doc(["ListWidget", "BarsWidget", "UnsupportedWidget"])) == [
        "additive: BoardSpec.widgets accepts BarsWidget as well"
    ]
    assert compare(before, doc(["ListWidget"])) == ["BREAKING: BoardSpec.widgets changed"]
    # Anything else changing under items is still a change to review.
    assert compare(before, doc(["ListWidget", "UnsupportedWidget"], {"maxItems": 5})) == [
        "BREAKING: BoardSpec.widgets changed"
    ]


def test_compare_accepts_an_embedded_default_gaining_the_fields_own_default() -> None:
    """Found adding ListDisplay.layout (default "rows"): ListWidget.display's embedded
    default gained "layout": "rows" — not null, but exactly the new field's default."""

    def doc(embedded: dict[str, Any], layout_default: str = "rows") -> dict[str, Any]:
        display = {"$ref": "#/$defs/ListDisplay", "default": embedded}
        child = {"properties": {"layout": {"default": layout_default, "enum": ["rows", "grid"]}}}
        return {
            "$defs": {
                "ListWidget": {"properties": {"display": display}},
                "ListDisplay": child,
            }
        }

    before = doc({"fields": ["name"]})
    before["$defs"]["ListDisplay"] = {"properties": {}}
    assert compare(before, doc({"fields": ["name"], "layout": "rows"})) == [
        "additive: ListDisplay.layout added (optional)",
        "additive: ListWidget.display default gains a new optional field at its default",
    ]
    # An embedded value that differs from the field's own default changes meaning.
    assert "BREAKING: ListWidget.display changed" in compare(
        before, doc({"fields": ["name"], "layout": "grid"})
    )
