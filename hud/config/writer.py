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
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

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
    buf = io.StringIO()
    yaml.dump(doc, buf)
    return buf.getvalue()


def has_explicit_start(text: str) -> bool:
    """ruamel does not remember a leading ``---``; detect it so write-back keeps it."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("%"):
            continue
        return stripped == "---" or stripped.startswith("--- ")
    return False


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
