# SPDX-License-Identifier: Apache-2.0
import shutil
from pathlib import Path

import pytest
from ruamel.yaml.comments import CommentedMap

from hud.config import ConfigError, ConfigManager
from hud.config.writer import IndentStyle
from tests.conftest import FIXTURES


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def test_load_then_save_unchanged_is_byte_identical(
    manager: ConfigManager, config_dir: Path
) -> None:
    target = config_dir / "settings.yaml"
    shutil.copy(FIXTURES / "roundtrip.yaml", target)
    original = target.read_bytes()

    manager.edit("settings.yaml", lambda _doc: None)

    assert target.read_bytes() == original


def test_edit_preserves_everything_outside_changed_region(
    manager: ConfigManager, config_dir: Path
) -> None:
    target = config_dir / "settings.yaml"
    shutil.copy(FIXTURES / "roundtrip.yaml", target)
    before = _lines(target.read_text())

    def mutate(doc: CommentedMap) -> None:
        doc["spec"]["theme"] = "light"

    snap = manager.edit("settings.yaml", mutate)
    after = _lines(target.read_text())

    assert snap.settings.spec.theme == "light"
    assert len(before) == len(after)
    changed = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    assert len(changed) == 1
    assert before[changed[0]] == "  theme: dark\n"
    assert after[changed[0]] == "  theme: light\n"
    # comments, anchor, alias, quotes and the unmapped key all survived verbatim
    text = "".join(after)
    assert "# Top-of-file comment" in text
    assert "&tz America/Chicago" in text
    assert "aliased_tz: *tz" in text
    assert 'title: "Quoted Title"' in text
    assert "fancy_feature: true" in text
    assert "- two   # comment on a list item" in text


def test_edit_adds_key_at_end_of_mapping_only(manager: ConfigManager, config_dir: Path) -> None:
    target = config_dir / "settings.yaml"
    shutil.copy(FIXTURES / "roundtrip.yaml", target)
    before = target.read_text()

    def mutate(doc: CommentedMap) -> None:
        doc["spec"]["retention"]["rollup_1d"] = "365d"

    manager.edit("settings.yaml", mutate)
    after = target.read_text()
    assert after.replace("rollup_1d: 365d", "rollup_1d: forever") == before


def test_explicit_document_start_is_kept(manager: ConfigManager, config_dir: Path) -> None:
    target = config_dir / "settings.yaml"
    target.write_text("---\napiVersion: hud/v1\nkind: Settings\nspec:\n  theme: dark\n")

    manager.edit("settings.yaml", lambda d: d["spec"].__setitem__("theme", "light"))

    assert target.read_text() == "---\napiVersion: hud/v1\nkind: Settings\nspec:\n  theme: light\n"


def test_write_is_atomic_no_temp_left_behind(manager: ConfigManager, config_dir: Path) -> None:
    target = config_dir / "settings.yaml"
    shutil.copy(FIXTURES / "roundtrip.yaml", target)
    manager.edit("settings.yaml", lambda d: d["spec"].__setitem__("theme", "auto"))
    assert [p.name for p in config_dir.iterdir()] == ["settings.yaml"]


def test_invalid_edit_is_refused_and_file_untouched(
    manager: ConfigManager, config_dir: Path
) -> None:
    target = config_dir / "settings.yaml"
    shutil.copy(FIXTURES / "roundtrip.yaml", target)
    original = target.read_bytes()

    with pytest.raises(ConfigError, match=r"spec\.theme"):
        manager.edit("settings.yaml", lambda d: d["spec"].__setitem__("theme", "neon"))
    assert target.read_bytes() == original

    with pytest.raises(ConfigError, match="literal secret"):
        manager.edit(
            "settings.yaml", lambda d: d["spec"].__setitem__("api_key", "0123456789abcdef" * 2)
        )
    assert target.read_bytes() == original


def test_indent_style_detection() -> None:
    assert IndentStyle.detect("a:\n  b:\n    - x\n") == IndentStyle(mapping=2, sequence=4, offset=2)
    assert IndentStyle.detect("a:\n  b:\n  - x\n") == IndentStyle(mapping=2, sequence=2, offset=0)
    assert IndentStyle.detect("a:\n    b: 1\n") == IndentStyle(mapping=4, sequence=2, offset=0)
    assert IndentStyle.detect("# only comments\n") == IndentStyle()


def test_default_style_sequences_round_trip(manager: ConfigManager, config_dir: Path) -> None:
    text = (
        "apiVersion: hud/v1\nkind: Settings\nspec:\n  theme: dark\n  extra:\n"
        "  - one\n  - two\n  nested:\n    deeper:\n    - x\n"
    )
    target = config_dir / "settings.yaml"
    target.write_text(text)
    manager.edit("settings.yaml", lambda _d: None)
    assert target.read_text() == text
