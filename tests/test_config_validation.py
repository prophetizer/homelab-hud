# SPDX-License-Identifier: Apache-2.0
import asyncio
from pathlib import Path

import pytest

from hud.config import ConfigError, ConfigManager, SecretNotFoundError, SecretResolver
from hud.config.secrets import looks_like_literal_secret

GOOD = "apiVersion: hud/v1\nkind: Settings\nspec:\n  theme: dark\n"


def _issues(exc: ConfigError) -> list[str]:
    return [str(i) for i in exc.issues]


def test_bootstrap_writes_settings_on_empty_dir(manager: ConfigManager, config_dir: Path) -> None:
    assert manager.bootstrap() is True
    assert (config_dir / "settings.yaml").exists()
    assert manager.bootstrap() is False  # idempotent
    snap = manager.load()
    assert snap.settings.spec.title == "HUD"
    assert snap.settings.spec.retention.samples == "48h"
    assert len(snap.version) == 12
    assert snap.warnings == ()


def test_yaml_syntax_error_names_file_and_line(manager: ConfigManager, config_dir: Path) -> None:
    f = config_dir / "settings.yaml"
    f.write_text("apiVersion: hud/v1\nkind: Settings\nspec:\n  theme: dark\n  title: [unclosed\n")
    with pytest.raises(ConfigError) as ei:
        manager.load()
    msgs = _issues(ei.value)
    assert len(msgs) == 1
    assert msgs[0].startswith(f"{f}:")
    assert "YAML syntax error" in msgs[0]
    assert ei.value.issues[0].line in (5, 6)  # ruamel reports at/after the offending flow seq


def test_tab_indentation_error_has_line(manager: ConfigManager, config_dir: Path) -> None:
    f = config_dir / "settings.yaml"
    f.write_text("apiVersion: hud/v1\nkind: Settings\nspec:\n\ttheme: dark\n")
    with pytest.raises(ConfigError) as ei:
        manager.load()
    assert ei.value.issues[0].line == 4


def test_schema_error_names_file_line_and_path(manager: ConfigManager, config_dir: Path) -> None:
    f = config_dir / "settings.yaml"
    f.write_text("apiVersion: hud/v1\nkind: Settings\nspec:\n  title: x\n  theme: neon\n")
    with pytest.raises(ConfigError) as ei:
        manager.load()
    (issue,) = ei.value.issues
    assert issue.file == f
    assert issue.line == 5
    assert issue.column == 3
    assert issue.message.startswith("spec.theme:")


def test_wrong_api_version(manager: ConfigManager, config_dir: Path) -> None:
    (config_dir / "settings.yaml").write_text("apiVersion: dashboard/v1\nkind: Settings\n")
    with pytest.raises(ConfigError) as ei:
        manager.load()
    assert ei.value.issues[0].line == 1
    assert "apiVersion" in ei.value.issues[0].message


def test_unknown_kind_in_a_content_folder_is_contained(
    manager: ConfigManager, config_dir: Path
) -> None:
    """A kind this image does not know, in its own folder, is what a newer image's config
    looks like to an older one: it degrades that file and the rest still loads (§11.3a)."""
    (config_dir / "settings.yaml").write_text(GOOD)
    (config_dir / "boards").mkdir()
    (config_dir / "boards" / "x.yaml").write_text("apiVersion: hud/v1\nkind: Report\nspec: {}\n")
    snap = manager.load()
    (q,) = snap.quarantined
    (issue,) = q.issues
    assert issue.file.name == "x.yaml"
    assert issue.line == 2
    assert "unsupported kind 'Report' (known: Board, Provider, RBAC, Settings)" in issue.message
    assert q.kind == "Report" and not q.serving_last_good


def test_unreadable_or_unrecognised_top_level_file_is_still_fatal(
    manager: ConfigManager, config_dir: Path
) -> None:
    """A top-level file whose kind cannot be read, or is not one HUD can contain, could be
    settings.yaml or rbac.yaml — so it is never guessed at, and startup still refuses."""
    (config_dir / "settings.yaml").write_text(GOOD)
    for label, text in (
        ("unparseable", "apiVersion: hud/v1\nkind: [unclosed\n"),
        ("unrecognised kind", "apiVersion: hud/v1\nkind: Widgetry\nspec: {}\n"),
    ):
        (config_dir / "x.yaml").write_text(text)
        with pytest.raises(ConfigError) as ei:
            manager.load()
        assert ei.value.issues[0].file.name == "x.yaml", label


def test_declared_content_kind_is_contained_wherever_it_lives(
    manager: ConfigManager, config_dir: Path
) -> None:
    # Readable and declaring a containable kind: it is not settings or RBAC, so a bad one
    # at the top level degrades only itself.
    (config_dir / "settings.yaml").write_text(GOOD)
    (config_dir / "x.yaml").write_text("apiVersion: hud/v1\nkind: Board\nmetadata: {name: BAD}\n")
    (q,) = manager.load().quarantined
    assert q.kind == "Board" and q.name == "BAD"


def test_unknown_spec_key_warns_but_loads(manager: ConfigManager, config_dir: Path) -> None:
    (config_dir / "settings.yaml").write_text(
        "apiVersion: hud/v1\nkind: Settings\nspec:\n  theme: dark\n  retenton:\n    samples: 1h\n"
    )
    snap = manager.load()
    (warning,) = snap.warnings
    assert warning.line == 5
    assert "unknown key 'spec.retenton'" in warning.message
    assert snap.settings.spec.retention.samples == "48h"


def test_unknown_envelope_key_fails(manager: ConfigManager, config_dir: Path) -> None:
    (config_dir / "settings.yaml").write_text(GOOD + "specs: {}\n")
    with pytest.raises(ConfigError) as ei:
        manager.load()
    assert ei.value.issues[0].line == 5
    assert "extra" in ei.value.issues[0].message.lower()


def test_missing_settings_reported(manager: ConfigManager, config_dir: Path) -> None:
    with pytest.raises(ConfigError, match=r"settings\.yaml is missing"):
        manager.load()


def test_all_errors_collected_across_files(manager: ConfigManager, config_dir: Path) -> None:
    (config_dir / "settings.yaml").write_text("apiVersion: hud/v1\nkind: Settings\nspec: [1]\n")
    (config_dir / "rbac.yaml").write_text("kind: [\n")
    with pytest.raises(ConfigError) as ei:
        manager.load()
    files = sorted(i.file.name for i in ei.value.issues)
    assert files == ["rbac.yaml", "settings.yaml"]


# ---------------------------------------------------------------------------- secrets


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("api_key", "hunter2"),
        ("apiKey", "hunter2"),
        ("password", "x"),
        ("client_secret", "abc"),
        ("token", "abc"),
        ("anything", "0123456789abcdef0123456789abcdef"),  # 32 hex
        ("url", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlLXNpZ25hdHVyZQ"),  # JWT
        ("note", "ghp_" + "A" * 36),
        ("id", "AKIAIOSFODNN7EXAMPLE"),
    ],
)
def test_literal_secret_detected(key: str, value: str) -> None:
    assert looks_like_literal_secret(key, value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("api_key", "${secret:sonarr_api_key}"),
        ("password", ""),
        ("title", "My Dashboard"),
        ("uid", "docker:container:sonarr"),
        ("timezone", "America/Chicago"),
        ("api_key", 42),
        ("url", "http://sonarr.lab:8989/api/v3"),
    ],
)
def test_non_secret_not_flagged(key: str, value: object) -> None:
    assert not looks_like_literal_secret(key, value)


def test_literal_secret_in_file_hard_fails_with_line(
    manager: ConfigManager, config_dir: Path
) -> None:
    (config_dir / "settings.yaml").write_text(
        GOOD + "  integrations:\n    sonarr:\n      api_key: 0123456789abcdef0123456789abcdef\n"
    )
    with pytest.raises(ConfigError) as ei:
        manager.load()
    (issue,) = ei.value.issues
    assert issue.line == 7
    assert "spec.integrations.sonarr.api_key" in issue.message
    assert "0123456789abcdef" not in issue.message, "never echo the secret back"


def test_secret_resolution_order(config_dir: Path, tmp_path: Path) -> None:
    secrets_dir = tmp_path / "run_secrets"
    secrets_dir.mkdir()
    (secrets_dir / "from_docker").write_text("docker-value\n")
    (config_dir / "secrets.yaml").write_text("from_file: file-value\nfrom_docker: shadowed\n")
    env = {"HUD_SECRET_FROM_ENV": "env-value", "HUD_SECRET_FROM_DOCKER": "shadowed"}
    r = SecretResolver(config_dir, secrets_dir=secrets_dir, env=env)

    assert r.resolve("from_docker") == "docker-value"  # trailing newline stripped
    assert r.resolve("from_env") == "env-value"
    assert r.resolve("from_file") == "file-value"
    with pytest.raises(SecretNotFoundError, match="'nope' not found"):
        r.resolve("nope")

    resolved = r.resolve_refs(
        {"a": "${secret:from_env}", "b": ["${secret:from_file}", "plain"], "c": 1}
    )
    assert resolved == {"a": "env-value", "b": ["file-value", "plain"], "c": 1}


def test_secrets_yaml_is_not_loaded_as_a_document(manager: ConfigManager, config_dir: Path) -> None:
    (config_dir / "settings.yaml").write_text(GOOD)
    (config_dir / "secrets.yaml").write_text("sonarr_api_key: 0123456789abcdef0123456789abcdef\n")
    snap = manager.load()
    assert [d.path.name for d in snap.documents] == ["settings.yaml"]
    with pytest.raises(ValueError, match="never written"):
        manager.edit("secrets.yaml", lambda d: None)


# ---------------------------------------------------------------------------- hot reload


async def test_hot_reload_installs_new_version_and_keeps_last_good_on_error(
    manager: ConfigManager, config_dir: Path
) -> None:
    f = config_dir / "settings.yaml"
    f.write_text(GOOD)
    v1 = manager.load().version
    seen: list[str] = []

    async def hook(snap) -> None:
        seen.append(snap.version)

    manager.on_reload(hook)
    task = asyncio.create_task(manager.watch())
    try:
        f.write_text(GOOD.replace("dark", "light"))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if manager.snapshot.version != v1:
                break
        assert manager.snapshot.version != v1
        assert manager.snapshot.settings.spec.theme == "light"
        assert seen == [manager.snapshot.version]
        v2 = manager.snapshot.version

        f.write_text("kind: Settings\napiVersion: hud/v1\nspec:\n  theme: neon\n")
        for _ in range(100):
            await asyncio.sleep(0.02)
            if manager.last_error is not None:
                break
        assert manager.last_error is not None
        assert manager.last_error.issues[0].line == 4
        assert manager.snapshot.version == v2, "last good snapshot must still be served"
        assert seen == [v2]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_unquoted_ref_in_flow_mapping_says_what_to_do(tmp_path: Path) -> None:
    """Found on the first live deployment: `config: { base_url: ${DOCKER_HOST} }` fails to
    parse because ${...} braces are YAML syntax inside a flow mapping. The parser's own
    message names the spot but not the cause; HUD's must say what to do (PLAN.md §14.2)."""
    from hud.config.loader import parse_yaml  # noqa: PLC0415

    text = "kind: Provider\nspec:\n  config: { base_url: ${DOCKER_HOST} }\n"
    with pytest.raises(ConfigError) as ei:
        parse_yaml(text, tmp_path / "docker.yaml")
    (issue,) = ei.value.issues
    assert issue.line == 3
    assert "must be quoted" in issue.message and "block style" in issue.message
    # The remedy it suggests actually works.
    parse_yaml(text.replace("${DOCKER_HOST}", '"${DOCKER_HOST}"'), tmp_path / "docker.yaml")
    # And an unrelated flow-mapping error gets no misleading hint.
    with pytest.raises(ConfigError) as ei:
        parse_yaml("a: { b: 1\nc: 2\n", tmp_path / "x.yaml")
    assert "must be quoted" not in str(ei.value)
