# SPDX-License-Identifier: Apache-2.0
"""JSON Schema for the frozen ``dashboard/v1`` configuration surface (PLAN.md §11.3).

Two jobs, one output:

* it is the machine-readable contract behind the ``dashboard/v1`` freeze — the checked-in
  copy at ``schema/dashboard-v1.json`` is compared against a fresh export in CI, so no
  change to the provider or board schema can land unnoticed;
* it is the release asset editors point at for completion (PLAN.md §15).

Run ``python -m hud.config.schema_export --write`` to regenerate after a *deliberate*
schema change; the test tells you the same thing when it fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from hud.config.schemas import (
    API_VERSION,
    BoardDocument,
    Document,
    ProviderDocument,
    RbacDocument,
    SettingsDocument,
)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "dashboard-v1.json"
SCHEMA_ID = "https://github.com/prophetizer/homelab-hud/schema/dashboard-v1.json"

# Frozen at v1: a change to either is a v2. Settings and RBAC are exported for editor
# completion but are explicitly *not* frozen — they may still gain keys (PLAN.md §11.3).
FROZEN = ("Provider", "Board")


def build_schema() -> dict[str, Any]:
    documents: dict[str, type[Document]] = {
        "Provider": ProviderDocument,
        "Board": BoardDocument,
        "Settings": SettingsDocument,
        "RBAC": RbacDocument,
    }
    # Pydantic emits each model's nested definitions under that model's own "$defs", but
    # `ref_template` points every "$ref" at the document root. Hoist them so the refs
    # resolve; the four document kinds share most of their nested models, and identical
    # definitions collapse onto one another by name.
    defs: dict[str, Any] = {}
    for kind, model in documents.items():
        schema = model.model_json_schema(mode="validation", ref_template="#/$defs/{model}")
        nested = schema.pop("$defs", {})
        for name, definition in nested.items():
            existing = defs.get(name)
            if existing is not None and existing != definition:
                msg = (
                    f"two different models are both named {name!r} in the exported schema; "
                    "rename one, or the published contract is ambiguous"
                )
                raise ValueError(msg)
            defs[name] = definition
        if kind in defs:
            msg = f"document kind {kind!r} collides with a nested model of the same name"
            raise ValueError(msg)
        defs[kind] = schema
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": f"HUD configuration ({API_VERSION})",
        "description": (
            "One schema per document kind. Provider and Board are frozen at "
            f"{API_VERSION}; Settings and RBAC may still gain optional keys."
        ),
        "x-frozen": list(FROZEN),
        "oneOf": [{"$ref": f"#/$defs/{kind}"} for kind in documents],
        "$defs": defs,
    }


def compare(published: dict[str, Any], fresh: dict[str, Any]) -> list[str]:
    """Changes between two exports, each classified by the §11.3a rules: ``additive`` (a
    new optional property, a new definition — a v1 minor may do this) or ``BREAKING``
    (anything that could refuse or reinterpret a config that validated before — v2)."""
    old_defs, new_defs = published.get("$defs", {}), fresh.get("$defs", {})
    out: list[str] = []
    for name in sorted(set(old_defs) | set(new_defs)):
        before, after = old_defs.get(name), new_defs.get(name)
        if before == after:
            continue
        if before is None:
            out.append(f"additive: new definition {name}")
        elif after is None:
            out.append(f"BREAKING: definition {name} removed")
        else:
            out.extend(_compare_definition(name, before, after))
    return out


def _compare_definition(name: str, before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    old_props, new_props = before.get("properties", {}), after.get("properties", {})
    old_req, new_req = set(before.get("required", [])), set(after.get("required", []))
    out = [f"BREAKING: {name}.{p} removed" for p in sorted(set(old_props) - set(new_props))]
    for p in sorted(set(old_props) & set(new_props)):
        a, b = old_props[p], new_props[p]
        if a == b:
            continue
        # A parent's embedded default copies a child's defaults, so a new optional field
        # in the child shows up here as the parent default gaining `"field": null` — the
        # same value the field defaults to. Not a break: every config means what it did.
        rest_same = {k: v for k, v in a.items() if k != "default"} == {
            k: v for k, v in b.items() if k != "default"
        }
        if rest_same and _only_null_keys_added(a.get("default"), b.get("default")):
            out.append(f"additive: {name}.{p} default gains null key(s) from a new optional field")
        else:
            out.append(f"BREAKING: {name}.{p} changed")
    for p in sorted(set(new_props) - set(old_props)):
        out.append(
            f"{'BREAKING' if p in new_req else 'additive'}: {name}.{p} added"
            + (" as required" if p in new_req else " (optional)")
        )
    out += [
        f"BREAKING: {name}.{p} became required"
        for p in sorted((new_req - old_req) & set(old_props))
    ]
    rest_before = {k: v for k, v in before.items() if k not in ("properties", "required")}
    rest_after = {k: v for k, v in after.items() if k not in ("properties", "required")}
    if rest_before != rest_after:
        out.append(f"BREAKING: {name} constraints changed (review)")
    return out


def _only_null_keys_added(old: Any, new: Any) -> bool:  # noqa: ANN401 — JSON values
    if isinstance(old, dict) and isinstance(new, dict):
        keep = all(k in new and _only_null_keys_added(v, new[k]) for k, v in old.items())
        return keep and all(new[k] is None for k in set(new) - set(old))
    return bool(old == new)


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help=f"write {SCHEMA_PATH} instead of printing it"
    )
    args = parser.parse_args(argv)
    text = render(build_schema())
    if args.write:
        SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCHEMA_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {SCHEMA_PATH}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
