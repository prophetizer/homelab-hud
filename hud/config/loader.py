# SPDX-License-Identifier: Apache-2.0
"""Round-trip YAML loading and Pydantic validation with file:line diagnostics.

``ruamel.yaml`` in round-trip mode is the only parser used anywhere in HUD (invariant 1):
the same object graph that is validated here is what the writer serializes back, so
comments, anchors, key order and unknown keys survive an edit.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import MarkedYAMLError, YAMLError

from hud.config.errors import ConfigError, ConfigIssue

log = logging.getLogger(__name__)


def new_yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096  # never re-wrap long scalars the user wrote on one line
    return yaml


def parse_yaml(text: str, file: Path) -> CommentedMap:
    """Parse one document into a round-trippable mapping. Syntax errors name file:line."""
    try:
        doc = new_yaml().load(text)
    except MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        line = mark.line + 1 if mark else None
        col = mark.column + 1 if mark else None
        problem = (exc.problem or exc.context or "invalid YAML").strip()
        raise ConfigError.single(file, f"YAML syntax error: {problem}", line, col) from exc
    except YAMLError as exc:
        raise ConfigError.single(file, f"YAML error: {exc}") from exc
    if doc is None:
        raise ConfigError.single(file, "file is empty", 1, 1)
    if not isinstance(doc, CommentedMap):
        raise ConfigError.single(file, "top level must be a mapping", 1, 1)
    return doc


def load_yaml(file: Path) -> CommentedMap:
    try:
        text = file.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError.single(file, f"not UTF-8: {exc.reason}") from exc
    return parse_yaml(text, file)


def locate(doc: Any, loc: tuple[int | str, ...]) -> tuple[int | None, int | None]:  # noqa: ANN401
    """Map a Pydantic error ``loc`` onto a 1-based (line, column) in the source document.

    Walks as deep as the document allows. A missing key points at its parent mapping's
    first key, which is the most useful place to send the user.
    """
    node: Any = doc
    line: int | None = None
    col: int | None = None
    parent_pos: tuple[int, int] | None = None
    for part in loc:
        if isinstance(node, CommentedMap) and isinstance(part, str):
            if part in node:
                key_line, key_col = node.lc.key(part)
                parent_pos = (key_line, key_col)
                line, col = key_line + 1, key_col + 1
                node = node[part]
                continue
            # Missing key: point at the parent mapping itself.
            if parent_pos is None and len(node) > 0:
                first_line, first_col = node.lc.key(next(iter(node)))
                line, col = first_line + 1, first_col + 1
            break
        if isinstance(node, CommentedSeq) and isinstance(part, int) and 0 <= part < len(node):
            item_line, item_col = node.lc.item(part)
            parent_pos = (item_line, item_col)
            line, col = item_line + 1, item_col + 1
            node = node[part]
            continue
        # Union tags and other non-structural loc parts: stop descending, keep last position.
        break
    return line, col


def validate[M: BaseModel](doc: CommentedMap, model: type[M], file: Path) -> M:
    """Validate ``doc`` against ``model``; every error names file:line:col."""
    try:
        return model.model_validate(doc)
    except ValidationError as exc:
        issues = []
        for err in exc.errors(include_url=False):
            line, col = locate(doc, tuple(err["loc"]))
            path = ".".join(str(p) for p in err["loc"]) or "<document>"
            issues.append(ConfigIssue(file, f"{path}: {err['msg']}", line, col))
        raise ConfigError(issues) from exc


def unknown_keys(doc: CommentedMap, model: type[BaseModel], file: Path) -> list[ConfigIssue]:
    """Keys present in ``doc`` that ``model`` does not declare, recursively, with positions.

    Only models with ``extra="allow"`` reach here with unknowns (forbid raises in
    :func:`validate`). Callers log these as warnings: unknown keys are preserved, never
    dropped, so a rollback to an older image never meets config it refuses (PLAN.md §11.3).
    """
    issues: list[ConfigIssue] = []
    _collect_unknown(doc, model, file, (), issues)
    return issues


def _collect_unknown(
    node: Any,  # noqa: ANN401
    model: type[BaseModel],
    file: Path,
    path: tuple[str, ...],
    out: list[ConfigIssue],
) -> None:
    if not isinstance(node, CommentedMap):
        return
    declared = {name: f for name, f in model.model_fields.items()}
    aliases = {f.alias: name for name, f in declared.items() if f.alias}
    for key in node:
        if not isinstance(key, str):
            continue
        name = aliases.get(key, key)
        if name not in declared:
            line, col = node.lc.key(key)
            dotted = ".".join((*path, key))
            out.append(ConfigIssue(file, f"unknown key '{dotted}' (ignored)", line + 1, col + 1))
            continue
        sub = declared[name].annotation
        if isinstance(sub, type) and issubclass(sub, BaseModel):
            _collect_unknown(node[key], sub, file, (*path, key), out)
