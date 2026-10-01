# SPDX-License-Identifier: Apache-2.0
"""Round-trip serialization and atomic file replacement.

A write is either fully visible or never happened: content goes to a temp file in the same
directory, is fsync'd, then ``os.replace``-d over the target. A crash mid-write leaves the
old file intact and at most a stray ``.<name>.*.tmp`` beside it.
"""

from __future__ import annotations

import io
import os
import tempfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from hud.config.loader import new_yaml


@dataclass(frozen=True)
class IndentStyle:
    """ruamel does not remember a file's indentation; we detect it so write-back matches."""

    mapping: int = 2
    sequence: int = 2
    offset: int = 0  # dash position relative to the parent key

    @classmethod
    def detect(cls, text: str) -> IndentStyle:
        mapping: int | None = None
        sequence: int | None = None
        offset: int | None = None
        prev_key_indent: int | None = None
        for raw in text.splitlines():
            stripped = raw.lstrip(" ")
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(raw) - len(stripped)
            if stripped.startswith("- "):
                if offset is None and prev_key_indent is not None and indent >= prev_key_indent:
                    offset = indent - prev_key_indent
                    # "- " then the item; ruamel's `sequence` is the item's column offset
                    sequence = offset + 2
                prev_key_indent = None
                continue
            if stripped.rstrip().endswith(":"):
                if mapping is None and prev_key_indent is not None and indent > prev_key_indent:
                    mapping = indent - prev_key_indent
                prev_key_indent = indent
            else:
                if mapping is None and prev_key_indent is not None and indent > prev_key_indent:
                    mapping = indent - prev_key_indent
                prev_key_indent = None
            if mapping is not None and offset is not None:
                break
        return cls(
            mapping=mapping or cls.mapping,
            sequence=sequence or cls.sequence,
            offset=offset if offset is not None else cls.offset,
        )


def dump_yaml(
    doc: CommentedMap, *, explicit_start: bool = False, indent: IndentStyle | None = None
) -> str:
    yaml = new_yaml()
    yaml.explicit_start = explicit_start
    style = indent or IndentStyle()
    yaml.indent(mapping=style.mapping, sequence=style.sequence, offset=style.offset)
    _keep_anchors(doc)
    buf = io.StringIO()
    yaml.dump(doc, buf)
    return buf.getvalue()


def _keep_anchors(node: object) -> None:
    """ruamel drops an anchor nothing aliases; the user wrote it, so it stays (PLAN §8.3)."""
    if isinstance(node, CommentedMap | CommentedSeq):
        if node.anchor.value:
            node.anchor.always_dump = True
        for child in node.values() if isinstance(node, CommentedMap) else node:
            _keep_anchors(child)


def has_explicit_start(text: str) -> bool:
    """ruamel does not remember a leading ``---``; detect it so write-back keeps it."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("%"):
            continue
        return stripped == "---" or stripped.startswith("--- ")
    return False


def keep_original_lines(original: str, base: str, rendered: str) -> str:
    """Give back the user's own text for every line an edit did not change.

    ruamel keeps comments and key order but not every byte: a flow mapping written
    ``{ col: 1 }`` comes back ``{col: 1}`` — everywhere in the file, on any write. So the
    edit is applied as a line diff: ``base`` is the file parsed and dumped untouched,
    ``rendered`` the same after the edit; lines the two share come from ``original``
    verbatim, changed lines from ``rendered`` (PLAN R9: byte-level preservation outside the
    edited region). When ruamel reflowed the file so its lines no longer pair one-to-one
    with the original, the rendered text is returned as before.
    """
    o = original.splitlines(keepends=True)
    b = base.splitlines(keepends=True)
    r = rendered.splitlines(keepends=True)
    if len(o) != len(b) or any(_squash(x) != _squash(y) for x, y in zip(o, b, strict=True)):
        return rendered
    out: list[str] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, b, r, autojunk=False).get_opcodes():
        out.extend(o[i1:i2] if tag == "equal" else r[j1:j2])
    return "".join(out)


def _squash(line: str) -> str:
    return "".join(line.split())


def atomic_write_text(path: Path, text: str, *, mode: int | None = None) -> None:
    """Write via a temp file and rename. ``mode`` forces the permissions (owner-only for
    secrets.yaml); otherwise an existing file keeps its own and a new one gets 0644."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    try:
        dfd = os.open(directory, os.O_RDONLY)
    except OSError:
        return  # some bind-mount filesystems refuse; the rename itself is durable enough
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)
