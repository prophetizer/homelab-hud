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
