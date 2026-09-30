# SPDX-License-Identifier: Apache-2.0
"""A theme.park palette as HUD's own stylesheet (PLAN §8.6, built 2026-09-29).

The backend fetches the theme's ``theme-options`` file from the configured theme.park
instance, keeps only theme.park's known colour variables, and writes a small stylesheet
that maps them onto HUD's surface and text tokens. The browser only ever loads
``/api/v1/theme.css`` from HUD itself.

Two rules keep this safe and honest:

- **Values are checked, not trusted.** A value must be a plain colour (hex, rgb/rgba,
  hsl/hsla, an RGB triplet) or a linear gradient of those; anything else — ``url(``, a
  ``}``, an ``@import``, an expression — drops that variable. A stylesheet from the network
  can therefore never add a selector or load anything.
- **Surfaces and text only.** Status colours are never mapped (invariant 9), and neither is
  theme.park's accent: HUD keeps colour for status.

With ``follow``, the theme's name comes from a theme picker's current-theme endpoint on
each refresh, so HUD changes when the rest of the stack does; while the picker cannot be
asked, the last name it gave (or ``theme``) is used.

A source that fails keeps the last good palette; one that has never answered serves an
empty stylesheet — HUD's default palette — and says why in the log (invariant 6).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from hud.config.schemas.duration import parse_duration
from hud.config.schemas.settings import THEME_NAME, ThemePark

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 5.0
MAX_BYTES = 64 * 1024
# theme.park keeps its own themes and community themes in two folders.
FOLDERS = ("theme-options", "community-theme-options")

_DECL = re.compile(r"--([a-z0-9-]+)\s*:\s*([^;{}]*);")
_HEX = r"#[0-9a-fA-F]{3,8}"
_NUM = r"-?\d{1,3}(?:\.\d+)?%?"
_FUNC = rf"(?:rgba?|hsla?)\(\s*{_NUM}(?:\s*[,/ ]\s*{_NUM}){{2,3}}\s*\)"
_COLOR = rf"(?:{_HEX}|{_FUNC}|transparent)"
_TRIPLET = re.compile(rf"\s*{_NUM}\s*,\s*{_NUM}\s*,\s*{_NUM}\s*")
_STOP = rf"{_COLOR}(?:\s+{_NUM})?"
_GRADIENT = re.compile(
    rf"linear-gradient\(\s*(?:-?\d{{1,3}}deg\s*,\s*)?{_STOP}(?:\s*,\s*{_STOP})+\s*\)"
)
_PLAIN = re.compile(_COLOR)

# theme.park variable -> the HUD tokens it becomes. Everything else in the file is ignored.
WANTED = (
    "main-bg-color",
    "modal-bg-color",
    "drop-down-menu-bg",
    "text",
    "text-muted",
)


def safe_value(v: str) -> str | None:
    """A colour HUD will use, or None. Triplets ("121, 184, 202") become rgb()."""
    v = v.strip()
    if _PLAIN.fullmatch(v) or _GRADIENT.fullmatch(v):
        return v
    if _TRIPLET.fullmatch(v):
        return f"rgb({v.strip()})"
    return None


def palette(css: str) -> dict[str, str]:
    """The known, safe variables a theme-options file defines (the last definition wins)."""
    found: dict[str, str] = {}
    for name, value in _DECL.findall(css):
        if name in WANTED and (ok := safe_value(value)) is not None:
            found[name] = ok
    return found


def _first_colour(v: str) -> str:
    """A gradient's first stop, for tokens that must be one colour (borders, mixes)."""
    m = _PLAIN.search(v)
    return m.group(0) if m and v.startswith("linear-gradient") else v


def _luminance(colour: str) -> float | None:
    """Relative lightness 0-1 of a hex or rgb() colour; None when it cannot be told."""
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", colour)
    if m:
        rgb = [int(m.group(1)[i : i + 2], 16) for i in (0, 2, 4)]
    else:
        m = re.fullmatch(r"rgba?\(\s*(\d+)\s*[, ]\s*(\d+)\s*[, ]\s*(\d+).*\)", colour)
        if not m:
            return None
        rgb = [int(x) for x in m.groups()]
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255


def stylesheet(theme: str, found: dict[str, str]) -> str:
    """HUD tokens from a palette. Applies under ``:root[data-themepark][data-theme]`` — more
    specific than the dark/light defaults, whatever order the stylesheets load in — and
    never touches ``--status-*``."""
    bg = found.get("main-bg-color")
    fg = found.get("text")
    if bg is None or fg is None:
        return f"/* theme.park {theme}: no background or text colour; HUD's palette stays */\n"
    base = _first_colour(bg)
    raised = _first_colour(found.get("modal-bg-color", f"color-mix(in srgb, {fg} 5%, {base})"))
    hover = _first_colour(found.get("drop-down-menu-bg", f"color-mix(in srgb, {fg} 9%, {base})"))
    muted = found.get("text-muted", f"color-mix(in srgb, {fg} 70%, {base})")
    light = (_luminance(base) or 0.0) > 0.6
    tokens = {
        "--bg-sunken": f"color-mix(in srgb, {base} 82%, {'white' if light else 'black'})",
        "--bg": base,
        "--bg-raised": raised,
        "--bg-hover": hover,
        "--border": f"color-mix(in srgb, {fg} 16%, {raised})",
        "--border-soft": f"color-mix(in srgb, {fg} 9%, {raised})",
        "--fg": fg,
        "--fg-muted": muted,
        "--fg-faint": f"color-mix(in srgb, {muted} 75%, {raised})",
        "--icon-mono-filter": "none" if light else "invert(0.82)",
    }
    lines = [f"  {k}: {v};" for k, v in tokens.items()]
    scope = ":root[data-themepark][data-theme]"
    page = (
        f"\n{scope} .main {{ background-color: {base}; background-image: {bg}; }}"
        if bg != base
        else ""
    )
    return (
        f"/* theme.park {theme}: surfaces and text only; status colours are HUD's own */\n"
        f"{scope} {{\n  color-scheme: {'light' if light else 'dark'};\n"
        + "\n".join(lines)
        + "\n}"
        + page
        + "\n"
    )


@dataclass
class _Cached:
    key: tuple[str, str]
    css: str
    fetched: float
    error: str | None = None


@dataclass
class ThemeParkStore:
    """The current theme as CSS, fetched on first use and again after ``refresh``."""

    transport: httpx.AsyncBaseTransport | None = None
    clock: Callable[[], float] = time.monotonic
    _cached: _Cached | None = None
    _followed: str | None = None  # the last theme name the picker gave
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def css(self, cfg: ThemePark | None) -> str:
        if cfg is None:
            return ""
        now = self.clock()
        c = self._cached
        if (
            c is not None
            and c.key[0] == cfg.source
            and now - c.fetched < parse_duration(cfg.refresh)
        ):
            return c.css
        async with self._lock:
            c = self._cached
            if (
                c is not None
                and c.key[0] == cfg.source
                and now - c.fetched < parse_duration(cfg.refresh)
            ):
                return c.css
            name = await self._theme_name(cfg)
            if name is None:
                self._cached = _Cached((cfg.source, ""), "", now, "no theme")
                return ""
            if c is not None and c.key == (cfg.source, name) and c.error is None:
                self._cached = _Cached(c.key, c.css, now)  # same theme: nothing to fetch
                return c.css
            try:
                css = stylesheet(name, palette(await self._fetch(cfg.source, name)))
                self._cached = _Cached((cfg.source, name), css, now)
                log.info("theme.park %s: palette loaded", name)
            except (httpx.HTTPError, ValueError) as exc:
                reason = f"{type(exc).__name__}: {exc}"
                keep = c.css if c is not None else ""
                self._cached = _Cached((cfg.source, name), keep, now, reason)
                log.warning(
                    "theme.park %s: %s; %s",
                    name,
                    reason,
                    "keeping the last palette" if keep else "HUD's own palette stays",
                )
            return self._cached.css

    async def _theme_name(self, cfg: ThemePark) -> str | None:
        """The theme to wear: the picker's current one, else the last it gave, else
        ``theme``."""
        if cfg.follow is None:
            return cfg.theme
        try:
            async with self._client() as client:
                resp = await client.get(cfg.follow)
                if resp.status_code != 200 or len(resp.content) > MAX_BYTES:
                    msg = f"HTTP {resp.status_code}"
                    raise ValueError(msg)
                name = resp.json().get("theme")
            if not isinstance(name, str) or not THEME_NAME.fullmatch(name):
                msg = "the picker's answer has no usable theme name"
                raise ValueError(msg)
            if name != self._followed:
                log.info("theme.park: the stack now wears %s", name)
            self._followed = name
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            fallback = self._followed or cfg.theme
            log.warning(
                "theme.park: cannot ask the picker (%s); wearing %s",
                f"{type(exc).__name__}: {exc}",
                fallback or "HUD's own palette",
            )
            return fallback
        return name

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(FETCH_TIMEOUT), follow_redirects=False, transport=self.transport
        )

    async def _fetch(self, source: str, name: str) -> str:
        async with self._client() as client:
            for folder in FOLDERS:
                resp = await client.get(f"{source}/css/{folder}/{name}.css")
                if resp.status_code == 404:
                    continue
                if resp.status_code != 200:
                    msg = f"HTTP {resp.status_code}"
                    raise ValueError(msg)
                if len(resp.content) > MAX_BYTES:
                    msg = "theme file too large"
                    raise ValueError(msg)
                return resp.text
        msg = f"no theme named {name!r} at this theme.park"
        raise ValueError(msg)


__all__ = ["ThemeParkStore", "palette", "safe_value", "stylesheet"]
