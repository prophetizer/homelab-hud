# SPDX-License-Identifier: Apache-2.0
"""Configuration errors always carry a file and, where known, a line."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConfigIssue:
    file: Path
    message: str
    line: int | None = None  # 1-based
    column: int | None = None  # 1-based

    def __str__(self) -> str:
        where = str(self.file)
        if self.line is not None:
            where += f":{self.line}"
            if self.column is not None:
                where += f":{self.column}"
        return f"{where}: {self.message}"


class ConfigError(Exception):
    """One or more issues. Raised at load time; startup refuses to continue."""

    def __init__(self, issues: Sequence[ConfigIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("\n".join(str(i) for i in self.issues))

    @classmethod
    def single(
        cls, file: Path, message: str, line: int | None = None, column: int | None = None
    ) -> ConfigError:
        return cls([ConfigIssue(file, message, line, column)])
