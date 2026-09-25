# SPDX-License-Identifier: Apache-2.0
"""Round-trip YAML loading and Pydantic validation with file:line diagnostics.

``ruamel.yaml`` in round-trip mode is the only parser used anywhere in HUD (invariant 1):
the same object graph that is validated here is what the writer serializes back, so
comments, anchors, key order and unknown keys survive an edit.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

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
        hint = _syntax_hint(text, line)
        message = f"YAML syntax error: {problem}" + (f" — {hint}" if hint else "")
        raise ConfigError.single(file, message, line, col) from exc
    except YAMLError as exc:
        raise ConfigError.single(file, f"YAML error: {exc}") from exc
    if doc is None:
        raise ConfigError.single(file, "file is empty", 1, 1)
    if not isinstance(doc, CommentedMap):
        raise ConfigError.single(file, "top level must be a mapping", 1, 1)
    return doc


_UNQUOTED_REF_IN_FLOW = re.compile(r"\{[^}\n]*?(?<![\"'])\$\{")


def _syntax_hint(text: str, line: int | None) -> str | None:
    """The one YAML trap every HUD user meets: ``${VAR}`` or ``${secret:name}`` unquoted
    inside a ``{ ... }`` flow mapping. Its braces are YAML syntax there, so the file fails
    to parse before substitution ever runs, and the parser's own message (expected ','
    or '}') does not say why (PLAN.md §14.2: say what to do)."""
    if line is None:
        return None
    lines = text.splitlines()
    if not 1 <= line <= len(lines) or not _UNQUOTED_REF_IN_FLOW.search(lines[line - 1]):
        return None
    return (
        'a ${...} reference inside { } must be quoted, e.g. { base_url: "${DOCKER_HOST}" }, '
        "or written in block style on its own line"
    )


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
        _descend(node[key], declared[name].annotation, file, (*path, key), out)


def _descend(
    node: Any,  # noqa: ANN401
    annotation: Any,  # noqa: ANN401
    file: Path,
    path: tuple[str, ...],
    out: list[ConfigIssue],
) -> None:
    """Follow ``annotation`` into ``node``: a model, a list of models, or a union of models
    discriminated on a ``type`` literal (the Board widget shape)."""
    origin = get_origin(annotation)
    if origin is Annotated:
        _descend(node, get_args(annotation)[0], file, path, out)
    elif origin is list and isinstance(node, CommentedSeq):
        (elem,) = get_args(annotation)
        for i, item in enumerate(node):
            _descend(item, elem, file, (*path, str(i)), out)
    elif origin in (Union, UnionType):
        member = _pick_union_member(node, get_args(annotation))
        if member is not None:
            _collect_unknown(node, member, file, path, out)
    elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
        _collect_unknown(node, annotation, file, path, out)


def _pick_union_member(node: Any, members: tuple[Any, ...]) -> type[BaseModel] | None:  # noqa: ANN401
    """The union member whose ``type`` literal matches ``node['type']``; the single model
    member when the union is ``Model | None``; otherwise None (do not guess)."""
    models: list[type[BaseModel]] = []
    for m in members:
        inner = get_args(m)[0] if get_origin(m) is Annotated else m
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            models.append(inner)
    if len(models) == 1:
        return models[0]
    tag = node.get("type") if isinstance(node, CommentedMap) else None
    if not isinstance(tag, str):
        return None
    fallback: type[BaseModel] | None = None
    for m in models:
        f = m.model_fields.get("type")
        if f is None:
            continue
        if get_origin(f.annotation) is Literal:
            if tag in get_args(f.annotation):
                return m
        elif f.annotation is str:
            fallback = m  # open-ended member (UnsupportedWidget): catch-all
    return fallback
