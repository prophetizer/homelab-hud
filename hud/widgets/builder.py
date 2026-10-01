# SPDX-License-Identifier: Apache-2.0
"""The widget builder (PLAN §8.5 Flow B): what can be shown, and the YAML edits that add,
change and remove a widget on a board.

The builder is an editor over the board file, never a second store. A new widget is
appended to ``spec.widgets`` as an ordinary mapping; an edit merges only the keys the form
changed into the existing mapping, so comments, key order and keys the form does not
understand stay exactly as written; a removal deletes the one entry in place (never a slice
assignment, which drops ruamel's comments).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from hud.collector import LiveCache
from hud.models import Resource

MAX_SAMPLES = 50  # resources listed per group; the count says how many there are


@dataclass
class Group:
    """Every resource of one kind from one provider — what a list, bars or status widget
    selects — with the metrics they carry (union) and the attrs they have."""

    provider: str
    kind: str
    count: int
    resources: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, str | None] = field(default_factory=dict)  # name → unit
    attrs: list[str] = field(default_factory=list)


def catalog(cache: LiveCache) -> list[Group]:
    groups: dict[tuple[str, str], Group] = {}
    attrs: dict[tuple[str, str], set[str]] = {}
    for r in sorted(cache.resources(), key=lambda r: (r.provider, r.kind, r.name.lower())):
        key = (r.provider, r.kind)
        g = groups.setdefault(key, Group(r.provider, r.kind, 0))
        g.count += 1
        if len(g.resources) < MAX_SAMPLES:
            g.resources.append(_brief(r))
        for m in cache.metrics_for(r.uid):
            g.metrics.setdefault(m.name, m.unit.value if m.unit else None)
        attrs.setdefault(key, set()).update(k for k in r.attrs if not k.startswith("homepage"))
    for key, g in groups.items():
        g.attrs = sorted(attrs.get(key, set()))
        g.metrics = dict(sorted(g.metrics.items()))
    return list(groups.values())


def _brief(r: Resource) -> dict[str, Any]:
    return {"uid": r.uid, "name": r.name, "state": r.state.value}


# ----------------------------------------------------------------------------- editing


def widget_entries(doc: CommentedMap) -> CommentedSeq:
    spec = doc.get("spec")
    if not isinstance(spec, CommentedMap):
        spec = CommentedMap()
        doc["spec"] = spec
    widgets = spec.get("widgets")
    if not isinstance(widgets, CommentedSeq):
        widgets = CommentedSeq()
        spec["widgets"] = widgets
    return widgets


def find(widgets: CommentedSeq, widget_id: str) -> int | None:
    for i, entry in enumerate(widgets):
        if isinstance(entry, CommentedMap) and entry.get("id") == widget_id:
            return i
    return None


def new_id(title: str | None, kind: str, taken: set[str]) -> str:
    """A readable, unique id: the title as a slug ("Plex bandwidth" → plex-bandwidth),
    else the widget type; -2, -3 … when taken."""
    base = re.sub(r"[^a-z0-9]+", "-", (title or kind).lower()).strip("-")[:40] or kind
    if not re.match(r"[a-z0-9]", base):
        base = f"w-{base}"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def placement(widgets: CommentedSeq, section: str | None, w: int, h: int) -> dict[str, int]:
    """Below everything already in the section (or among the loose tiles), left-aligned."""
    bottom = 0
    for entry in widgets:
        if not isinstance(entry, CommentedMap) or entry.get("section") != section:
            continue
        grid = entry.get("grid")
        if isinstance(grid, CommentedMap):
            bottom = max(bottom, int(grid.get("row", 1)) + int(grid.get("h", 1)) - 1)
    return {"col": 1, "row": bottom + 1, "w": w, "h": h}


def to_node(value: Any, key: str | None = None) -> Any:  # noqa: ANN401
    """Plain JSON → ruamel nodes, written the way the bundled boards are: short mappings
    (grid, a select, a stat) on one line, longer ones as blocks."""
    if isinstance(value, dict):
        node = CommentedMap()
        for k, v in value.items():
            node[k] = to_node(v, k)
        scalars = all(not isinstance(v, dict | list) or _flat_list(v) for v in value.values())
        if key in ("grid", "select") or (scalars and len(value) <= 4):
            node.fa.set_flow_style()
        return node
    if isinstance(value, list):
        seq = CommentedSeq(to_node(v) for v in value)
        if _flat_list(value):
            seq.fa.set_flow_style()
        return seq
    return value


def _flat_list(value: Any) -> bool:  # noqa: ANN401
    return isinstance(value, list) and all(not isinstance(v, dict | list) for v in value)


def merge(target: CommentedMap, changes: dict[str, Any]) -> None:
    """Apply a form's changes to an existing mapping in place: a key set to None is removed,
    a mapping merges into a mapping, anything else replaces. Keys not mentioned — including
    ones the form does not know — are left exactly as written, comments and all."""
    for key, value in changes.items():
        if value is None:
            if key in target:
                del target[key]
            continue
        current = target.get(key)
        if isinstance(value, dict) and isinstance(current, CommentedMap):
            merge(current, value)
        else:
            target[key] = to_node(value, key)


def plain(node: Any) -> Any:  # noqa: ANN401
    """A ruamel node as plain JSON, for the edit form."""
    if isinstance(node, dict):
        return {str(k): plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [plain(v) for v in node]
    return node


__all__ = [
    "Group",
    "catalog",
    "find",
    "merge",
    "new_id",
    "placement",
    "plain",
    "to_node",
    "widget_entries",
]
