# SPDX-License-Identifier: Apache-2.0
"""``kind: Board`` validation: the §8.2 example loads; later-phase types degrade to a tile."""

from pathlib import Path

from hud.config import ConfigManager
from hud.config.schemas import (
    BoardDocument,
    EmbedWidget,
    ListWidget,
    MetricWidget,
    UnsupportedWidget,
)
from hud.config.schemas.board import ChartWidget, UptimeWidget
from tests.conftest import FIXTURES

SETTINGS = "apiVersion: hud/v1\nkind: Settings\n"

MINIMAL = """\
apiVersion: hud/v1
kind: Board
metadata:
  name: infra
spec:
  widgets:
    - id: note
      type: static
      grid: { col: 1, row: 1 }
      display: { text: hello }
"""


def _load(manager: ConfigManager, config_dir: Path, text: str) -> BoardDocument:
    (config_dir / "settings.yaml").write_text(SETTINGS)
    (config_dir / "boards").mkdir(exist_ok=True)
    (config_dir / "boards" / "b.yaml").write_text(text)
    snap = manager.load()
    (doc,) = [d for d in snap.documents if d.model.kind == "Board"]
    assert isinstance(doc.model, BoardDocument)
    return doc.model


def _issues(manager: ConfigManager, config_dir: Path, text: str) -> list[str]:
    (config_dir / "settings.yaml").write_text(SETTINGS)
    (config_dir / "boards").mkdir(exist_ok=True)
    (config_dir / "boards" / "b.yaml").write_text(text)
    # An invalid Board is quarantined, not fatal (invariant 6): the load succeeds, the
    # file is refused and not loaded, and its issues are reported with file:line.
    snap = manager.load()
    assert not [d for d in snap.documents if isinstance(d.model, BoardDocument)], (
        "invalid file loaded"
    )
    (quarantined,) = snap.quarantined
    assert quarantined.kind == "Board" and not quarantined.serving_last_good
    return [str(i) for i in quarantined.issues]


def test_plan_example_loads(manager: ConfigManager, config_dir: Path) -> None:
    doc = _load(manager, config_dir, (FIXTURES / "boards" / "media.yaml").read_text())
    assert doc.metadata.name == "media"
    assert doc.metadata.title == "Media Stack"
    assert doc.metadata.icon == "mdi:filmstrip"
    assert doc.metadata.visible_to == ["admins", "household"]
    assert doc.spec.layout.columns.lg == 4
    by_id = {w.id: w for w in doc.spec.widgets}
    assert isinstance(by_id["arr-health"], ListWidget)
    assert by_id["arr-health"].source.select.provider == ["sonarr", "radarr", "lidarr", "prowlarr"]
    assert isinstance(by_id["queue-depth"], MetricWidget)
    assert by_id["queue-depth"].display.thresholds[1].gte == 200
    assert isinstance(by_id["tautulli"], EmbedWidget)
    assert by_id["tautulli"].display.open_in == "workspace"
    # The plan's uptime widget (§8) validates as the real type since Phase 2 slice 1.
    assert isinstance(by_id["plex-uptime"], UptimeWidget)
    assert by_id["plex-uptime"].source.resource == "plex:service:main"
    assert (by_id["plex-uptime"].display.range, by_id["plex-uptime"].display.buckets) == ("90d", 90)
    # The plan's chart (§8.2) validates as the real type since 2026-09-28.
    chart = by_id["net-throughput"]
    assert isinstance(chart, ChartWidget)
    assert [s.label for s in chart.source.series] == ["Down", "Up"]
    assert (chart.source.range, chart.source.tier) == ("24h", "auto")
    assert (chart.display.kind, chart.display.stacked, chart.display.unit) == ("area", False, "bps")
    assert doc.unsupported_widgets == []
    assert manager.snapshot.warnings == ()


def test_unknown_type_is_kept_and_named(manager: ConfigManager, config_dir: Path) -> None:
    doc = _load(manager, config_dir, MINIMAL.replace("type: static", "type: hologram"))
    (w,) = doc.spec.widgets
    assert isinstance(w, UnsupportedWidget)
    assert w.reason == "unknown widget type 'hologram'"
    assert w.display == {"text": "hello"}


def test_duplicate_widget_ids(manager: ConfigManager, config_dir: Path) -> None:
    text = MINIMAL + "    - id: note\n      type: static\n      grid: { col: 2, row: 1 }\n"
    (msg,) = _issues(manager, config_dir, text)
    assert "duplicate widget ids: note" in msg


def test_missing_source_names_widget_and_line(manager: ConfigManager, config_dir: Path) -> None:
    text = MINIMAL.replace("type: static", "type: resource")
    (msg,) = _issues(manager, config_dir, text)
    assert "boards/b.yaml:7" in msg
    assert "spec.widgets.0.resource.source" in msg


def test_embed_url_must_be_absolute(manager: ConfigManager, config_dir: Path) -> None:
    text = MINIMAL.replace("type: static", "type: embed").replace(
        "display: { text: hello }", "source: { url: /relative }"
    )
    (msg,) = _issues(manager, config_dir, text)
    assert "embed url must be absolute" in msg


def test_bad_sort_key(manager: ConfigManager, config_dir: Path) -> None:
    text = MINIMAL.replace("type: static", "type: list").replace(
        "display: { text: hello }", 'source: { sort: ["-uptime"] }'
    )
    (msg,) = _issues(manager, config_dir, text)
    assert "unsupported sort key '-uptime'" in msg


def test_unknown_key_in_widget_warns_with_line(manager: ConfigManager, config_dir: Path) -> None:
    text = MINIMAL + "      colour: red\n"
    _load(manager, config_dir, text)
    (warning,) = manager.snapshot.warnings
    assert "unknown key 'spec.widgets.0.colour'" in warning.message
    assert warning.line == 11


def test_board_name_is_a_slug(manager: ConfigManager, config_dir: Path) -> None:
    (msg,) = _issues(manager, config_dir, MINIMAL.replace("name: infra", "name: My Board"))
    assert "board name must be lowercase" in msg
