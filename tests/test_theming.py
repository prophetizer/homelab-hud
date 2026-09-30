# SPDX-License-Identifier: Apache-2.0
"""theme.park inside HUD (PLAN §8.6): only known, plain colours are taken from a theme file,
they become surface and text tokens (never status, never the accent), the stack's current
theme is followed through the picker, and a source that fails keeps the last palette or
HUD's own — never a broken page."""

from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from hud.api.spa import with_theme
from hud.config.schemas.settings import ThemePark
from hud.main import create_app
from hud.theming import ThemeParkStore, palette, safe_value, stylesheet
from tests.test_api_health import _env

SOURCE = "http://theme-park:80"
PICKER = "http://theme-picker:8090/api/current"
# The shape of a theme.park theme-options file (a theme's :root variables).
CONTRAST = """/* organizr-contrast */
:root {
  --main-bg-color: #1f1f1f;
  --modal-bg-color: #333333;
  --drop-down-menu-bg: #1b1b1b;
  --accent-color: 44, 171, 227;
  --text: #b6c2d5;
  --text-muted: #a3a3a3;
  --petio-spinner: invert(83%) sepia(9%);
}
"""
NORD = ":root { --main-bg-color: #2E3440; --modal-bg-color: #3B4252; --text: #D8DEE9; }"


@pytest.mark.parametrize(
    ("value", "kept"),
    [
        ("#1f1f1f", "#1f1f1f"),
        ("rgba(46, 52, 64, 0.17)", "rgba(46, 52, 64, 0.17)"),
        ("121, 184, 202", "rgb(121, 184, 202)"),
        (
            "linear-gradient(180deg, #1f1f1f 0%, #333333 100%)",
            "linear-gradient(180deg, #1f1f1f 0%, #333333 100%)",
        ),
        ("url(https://attacker.example/x.png)", None),
        ("#fff; } body { display: none", None),
        ("var(--accent-color)", None),
        ("expression(alert(1))", None),
        ("invert(83%) sepia(9%)", None),
    ],
)
def test_only_plain_colours_are_taken(value: str, kept: str | None) -> None:
    assert safe_value(value) == kept


def test_the_palette_keeps_surfaces_and_text_never_accent_or_status() -> None:
    found = palette(CONTRAST + ":root { --status-down: #00ff00; }")
    assert found == {
        "main-bg-color": "#1f1f1f",
        "modal-bg-color": "#333333",
        "drop-down-menu-bg": "#1b1b1b",
        "text": "#b6c2d5",
        "text-muted": "#a3a3a3",
    }
    css = stylesheet("organizr-contrast", found)
    assert ":root[data-themepark]" in css and "--bg: #1f1f1f;" in css and "--fg: #b6c2d5;" in css
    assert "status" not in css.split("*/", 1)[1] and "44, 171, 227" not in css
    assert "color-scheme: dark" in css


def test_a_gradient_background_and_a_light_theme() -> None:
    css = stylesheet(
        "dawn",
        palette(
            ":root { --main-bg-color: linear-gradient(90deg, #faf4ed, #fffaf3); --text: #575279; }"
        ),
    )
    assert "--bg: #faf4ed;" in css and ":root[data-themepark][data-theme] .main {" in css
    assert "color-scheme: light" in css and "--icon-mono-filter: none;" in css


def test_a_theme_without_background_or_text_changes_nothing() -> None:
    assert ":root" not in stylesheet("odd", palette(":root { --text-muted: #999999; }"))


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@respx.mock
async def test_following_the_picker_changes_the_theme_when_the_stack_does() -> None:
    picker = respx.get(PICKER).mock(
        return_value=httpx.Response(200, json={"theme": "organizr-contrast", "mode": "dark"})
    )
    respx.get(f"{SOURCE}/css/theme-options/organizr-contrast.css").mock(
        return_value=httpx.Response(200, text=CONTRAST)
    )
    respx.get(f"{SOURCE}/css/theme-options/nord.css").mock(return_value=httpx.Response(404))
    respx.get(f"{SOURCE}/css/community-theme-options/nord.css").mock(
        return_value=httpx.Response(200, text=NORD)
    )
    clock = Clock()
    store = ThemeParkStore(clock=clock)
    cfg = ThemePark(source=SOURCE, follow=PICKER)
    assert "--bg: #1f1f1f;" in await store.css(cfg)
    assert "--bg: #1f1f1f;" in await store.css(cfg)  # within refresh: not asked again
    assert picker.call_count == 1
    picker.mock(return_value=httpx.Response(200, json={"theme": "nord"}))
    clock.t += 61
    assert "--bg: #2E3440;" in await store.css(cfg)  # found in the community folder


@respx.mock
async def test_a_picker_or_source_that_fails_keeps_the_last_palette() -> None:
    picker = respx.get(PICKER).mock(
        return_value=httpx.Response(200, json={"theme": "organizr-contrast"})
    )
    theme = respx.get(f"{SOURCE}/css/theme-options/organizr-contrast.css").mock(
        return_value=httpx.Response(200, text=CONTRAST)
    )
    clock = Clock()
    store = ThemeParkStore(clock=clock)
    cfg = ThemePark(source=SOURCE, follow=PICKER, theme="nord")
    first = await store.css(cfg)
    picker.mock(side_effect=httpx.ConnectError("down"))
    clock.t += 61
    assert await store.css(cfg) == first  # the last theme the picker gave, not the fallback
    theme.mock(side_effect=httpx.ConnectError("down"))
    picker.mock(return_value=httpx.Response(200, json={"theme": "organizr-contrast"}))
    clock.t += 61
    assert await store.css(cfg) == first


@respx.mock
async def test_nothing_answers_ever_so_hud_keeps_its_own_palette() -> None:
    respx.get(PICKER).mock(return_value=httpx.Response(200, json={"theme": "../../etc/passwd"}))
    store = ThemeParkStore()
    assert await store.css(ThemePark(source=SOURCE, follow=PICKER)) == ""
    assert await store.css(None) == ""


@respx.mock
def test_the_endpoint_and_the_page_carry_the_theme(tmp_path: Path) -> None:
    env = _env(tmp_path)
    env.config_dir.mkdir(parents=True, exist_ok=True)
    (env.config_dir / "settings.yaml").write_text(
        "apiVersion: hud/v1\nkind: Settings\nspec:\n"
        f"  theme_park:\n    source: {SOURCE}\n    theme: organizr-contrast\n"
    )
    respx.get(f"{SOURCE}/css/theme-options/organizr-contrast.css").mock(
        return_value=httpx.Response(200, text=CONTRAST)
    )
    with TestClient(create_app(env)) as client:
        css = client.get("/api/v1/theme.css")  # no session: the sign-in page wears it too
        assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
        assert "--bg: #1f1f1f;" in css.text
        page = with_theme('<html lang="en">', client.app.state.config)  # type: ignore[attr-defined]
        assert page == '<html data-theme="dark" data-themepark lang="en">'
