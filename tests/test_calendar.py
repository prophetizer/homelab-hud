# SPDX-License-Identifier: Apache-2.0
"""Release calendar: a moving request window, the next release picked per film, and rows
placed on the right day — a release *date* is never shifted into the evening before."""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from hud.collector import LiveCache
from hud.config import SecretResolver
from hud.providers import ProviderBuildError, ProviderContext
from hud.providers.declarative import build_declarative
from hud.widgets import WidgetEngine
from hud.widgets.engine import when
from tests.test_templates import BASE, TEMPLATES, _load
from tests.test_widgets_engine import T0, _board, board, res

CHICAGO = ZoneInfo("America/Chicago")


async def _poll_calendar(config_dir: Path, service: str) -> tuple[httpx.Request, dict[str, dict]]:
    doc = _load(config_dir, TEMPLATES / f"{service}.yaml")
    env = {f"{service.upper()}_BASE_URL": BASE, f"HUD_SECRET_{service.upper()}_API_KEY": "t"}
    secrets = SecretResolver(config_dir, config_dir / "none", env=env)
    ctx = ProviderContext.create(service, secrets, env)
    provider = build_declarative(doc, ctx)
    body = (TEMPLATES / "fixtures" / service / "calendar.json").read_text()
    await provider.startup()
    try:
        with respx.mock() as mock:
            json_type = {"content-type": "application/json"}
            route = mock.get(f"{BASE}/api/v3/calendar").mock(
                return_value=httpx.Response(200, text=body, headers=json_type)
            )
            result = await provider.poll("calendar")
    finally:
        await provider.shutdown()
    return route.calls.last.request, {r.uid: r.attrs for r in result.resources}


async def test_sonarr_calendar_asks_for_a_window_that_moves_with_now(config_dir: Path) -> None:
    req, attrs = await _poll_calendar(config_dir, "sonarr")
    start = datetime.fromisoformat(req.url.params["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(req.url.params["end"].replace("Z", "+00:00"))
    now = datetime.now(UTC)
    assert abs((now - start).total_seconds() - 86_400) < 120
    assert abs((end - now).total_seconds() - 14 * 86_400) < 120
    ep = attrs["sonarr:upcoming:12-s2e5"]
    assert ep["show"] == "The Long Orbit"
    assert ep["episode"] == "S02E05 · The Quiet Shift"
    assert ep["image"] == "/MediaCover/12/poster-500.jpg?lastWrite=1"
    assert attrs["sonarr:upcoming:7-s1e4"]["episode"] == "S01E04"  # untitled episode


async def test_radarr_calendar_picks_the_next_release_in_the_window(config_dir: Path) -> None:
    _, attrs = await _poll_calendar(config_dir, "radarr")
    film = attrs["radarr:upcoming:550001"]
    # In cinemas 2099-09-01, digital 2099-10-03, physical 2099-11-10: all still ahead, so
    # the next one is the earliest.
    assert film["air_at"] == "2099-09-01T00:00:00Z"
    assert film["release"] == "In cinemas"
    assert film["image"] == "/MediaCover/41/poster-500.jpg?lastWrite=2"
    old = attrs["radarr:upcoming:550002"]
    assert old["air_at"] is None and old["release"] is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # An episode at 01:00 UTC airs the evening before in Chicago.
        ("2026-09-28T01:00:00Z", {"day": "2026-09-27", "time": "20:00"}),
        # A release day (midnight UTC) stays that day, with no time.
        ("2026-10-03T00:00:00Z", {"day": "2026-10-03", "time": None}),
        ("2026-10-03", {"day": "2026-10-03", "time": None}),
        (1_790_000_000, {"day": "2026-09-21", "time": "09:13"}),
        ("", None),
        ("soon", None),
        (None, None),
    ],
)
def test_when_places_a_row_on_its_day(value: object, expected: dict | None) -> None:
    assert when(value, CHICAGO) == expected


AGENDA = """
- id: upcoming
  type: list
  title: Coming up
  grid: { col: 1, row: 1, w: 4, h: 4 }
  source:
    select: { kind: upcoming }
    sort: [attrs.air_at]
  display: { layout: agenda, date: attrs.air_at, image: true, fields: [attrs.episode] }
"""


async def test_agenda_rows_carry_their_day_and_the_boards_today(config_dir: Path) -> None:
    cache = LiveCache()
    cache.apply(
        "sonarr",
        "calendar",
        [
            res("sonarr:upcoming:1-s1e2", air_at="2026-09-28T01:00:00Z", image="/p.jpg"),
            res("sonarr:upcoming:1-s1e1", air_at="2026-09-21T01:00:00Z"),
        ],
        [],
    )
    doc = board(config_dir, _board(AGENDA))
    engine = WidgetEngine(cache, None, timezone=lambda: "America/Chicago")
    (w,) = (await engine.resolve_board(doc, T0)).widgets
    items = w.data["items"]
    assert [i["uid"] for i in items] == ["sonarr:upcoming:1-s1e1", "sonarr:upcoming:1-s1e2"]
    assert items[1]["when"] == {"day": "2026-09-27", "time": "20:00"}
    assert items[1]["image"] is True and items[0]["image"] is False
    assert w.data["layout"] == "agenda"
    assert len(w.data["today"]) == 10


def test_a_broken_param_expression_fails_the_build(config_dir: Path) -> None:
    text = (
        (TEMPLATES / "sonarr.yaml")
        .read_text()
        .replace('start: "{{ utcnow(days=-1) }}"', 'start: "{{ utcnow(days=-1 }}"')
    )
    broken = config_dir.parent / "sonarr.yaml"
    broken.write_text(text)
    doc = _load(config_dir, broken)
    env = {"SONARR_BASE_URL": BASE, "HUD_SECRET_SONARR_API_KEY": "t"}
    secrets = SecretResolver(config_dir, config_dir / "none", env=env)
    with pytest.raises(ProviderBuildError, match="param start"):
        build_declarative(doc, ProviderContext.create("sonarr", secrets, env))
