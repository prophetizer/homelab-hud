# SPDX-License-Identifier: Apache-2.0
"""JSONPath ``select`` (PLAN.md §7.1) behind a typed facade over ``jsonpath-ng``.

Selection semantics: the matches of the path are the items to map — except when there is
exactly one match and it is a list, in which case *its elements* are the items. That makes
``$`` on a list response, ``$.records`` on an envelope, ``$[*]`` and ``$[?(…)]`` all do the
obvious thing without the author having to know which form the library returns.
"""

from __future__ import annotations

from typing import Any

from jsonpath_ng.exceptions import JsonPathParserError
from jsonpath_ng.ext import parse as _parse


class SelectError(ValueError):
    """The path did not parse. Raised at provider build time, never per poll."""


class Selector:
    __slots__ = ("_path", "source")

    def __init__(self, source: str) -> None:
        self.source = source
        try:
            self._path = _parse(source)
        except (JsonPathParserError, Exception) as exc:
            msg = f"invalid JSONPath {source!r}: {exc}"
            raise SelectError(msg) from exc

    def items(self, data: Any) -> list[Any]:  # noqa: ANN401
        matches = [m.value for m in self._path.find(data)]
        if len(matches) == 1 and isinstance(matches[0], list):
            return list(matches[0])
        return matches

    def first(self, data: Any) -> Any:  # noqa: ANN401
        """First match or None; used for pagination cursors."""
        found = self._path.find(data)
        return found[0].value if found else None
