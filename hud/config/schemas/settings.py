# SPDX-License-Identifier: Apache-2.0
"""``settings.yaml`` — global options (PLAN.md §11.2). Phase 0 carries only what exists."""

import ipaddress
import re
from typing import Literal, Self
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hud.config.schemas.base import Document
from hud.config.schemas.board import HeroStat
from hud.config.schemas.duration import parse_duration
from hud.config.secrets import is_secret_ref


class Retention(BaseModel):
    """Retention per tier (PLAN.md §6.2). Stored as the user wrote them; parsed on demand."""

    model_config = ConfigDict(extra="allow")

    samples: str = "48h"
    rollup_5m: str = "14d"
    rollup_1h: str = "180d"
    rollup_1d: str = "forever"

    @field_validator("samples", "rollup_5m", "rollup_1h", "rollup_1d")
    @classmethod
    def _valid_duration(cls, v: str) -> str:
        parse_duration(v)
        return v


# ---------------------------------------------------------------------------- auth (§10.1)

AuthBackendName = Literal["local", "forward", "oidc"]


class SessionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cookie_name: str = "hud_session"
    lifetime: str = "7d"

    @field_validator("cookie_name")
    @classmethod
    def _cookie_token(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", v):
            msg = "cookie_name must be [A-Za-z0-9_-]"
            raise ValueError(msg)
        return v

    @field_validator("lifetime")
    @classmethod
    def _valid_lifetime(cls, v: str) -> str:
        if parse_duration(v) == 0:
            msg = "session lifetime cannot be 'forever'"
            raise ValueError(msg)
        return v


class ForwardAuthSettings(BaseModel):
    """Trusted-proxy header auth. Header names are config so any forward-auth provider fits."""

    model_config = ConfigDict(extra="forbid")

    trusted_proxies: list[str] = []
    header_user: str = "Remote-User"
    header_groups: str = "Remote-Groups"
    header_email: str = "Remote-Email"
    header_name: str = "Remote-Name"
    groups_separator: str = ","

    @field_validator("trusted_proxies")
    @classmethod
    def _valid_networks(cls, v: list[str]) -> list[str]:
        for net in v:
            try:
                ipaddress.ip_network(net, strict=False)
            except ValueError as exc:
                msg = f"trusted_proxies entry {net!r} is not an IP address or CIDR"
                raise ValueError(msg) from exc
        return v


class OidcSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discovery_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    scopes: list[str] = ["openid", "profile", "email", "groups"]
    groups_claim: str = "groups"  # dotted path into the ID token / userinfo claims
    # Where the IdP sends the browser back. Defaults to <request origin>/api/v1/auth/oidc/callback;
    # set it when the app sits behind a proxy that rewrites the host.
    redirect_url: str | None = None

    @field_validator("client_secret")
    @classmethod
    def _secret_ref(cls, v: str) -> str:
        if v and not is_secret_ref(v):
            msg = "client_secret must be a ${secret:<name>} reference, never a literal"
            raise ValueError(msg)
        return v

    @field_validator("discovery_url")
    @classmethod
    def _https_discovery(cls, v: str) -> str:
        if v and not v.startswith(("https://", "http://")):
            msg = "discovery_url must be an http(s) URL"
            raise ValueError(msg)
        return v


class LocalAuthSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_registration: bool = False
    min_password_length: int = 12

    @field_validator("min_password_length")
    @classmethod
    def _sane_min(cls, v: int) -> int:
        if v < 8:
            msg = "min_password_length below 8 is not accepted"
            raise ValueError(msg)
        return v


class AuthSettings(BaseModel):
    """Which identity backends run, in precedence order (PLAN.md §10.1)."""

    model_config = ConfigDict(extra="forbid")

    backends: list[AuthBackendName] = ["local"]
    session: SessionSettings = SessionSettings()
    forward: ForwardAuthSettings = ForwardAuthSettings()
    oidc: OidcSettings = OidcSettings()
    local: LocalAuthSettings = LocalAuthSettings()

    @field_validator("backends")
    @classmethod
    def _non_empty_unique(cls, v: list[AuthBackendName]) -> list[AuthBackendName]:
        if not v:
            msg = (
                "auth.backends is empty; HUD refuses to run without authentication. "
                "Use [local] for a zero-infrastructure deployment"
            )
            raise ValueError(msg)
        if len(set(v)) != len(v):
            msg = "auth.backends lists a backend twice"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _backend_prerequisites(self) -> "AuthSettings":
        if "forward" in self.backends and not self.forward.trusted_proxies:
            msg = (
                "auth.forward.trusted_proxies is empty; the forward backend fails closed "
                "and would reject every request"
            )
            raise ValueError(msg)
        if "oidc" in self.backends:
            missing = [
                k
                for k in ("discovery_url", "client_id", "client_secret")
                if not getattr(self.oidc, k)
            ]
            if missing:
                msg = f"auth.oidc is enabled but {', '.join(missing)} not set"
                raise ValueError(msg)
        return self


# Web search from the header and Ctrl-K. The query goes from the browser straight to the
# engine; HUD never sees it. ``{q}`` is replaced with the URL-encoded query.
SEARCH_ENGINES = {
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "google": "https://www.google.com/search?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "startpage": "https://www.startpage.com/do/search?query={q}",
    "kagi": "https://kagi.com/search?q={q}",
    "brave": "https://search.brave.com/search?q={q}",
}
_BACKGROUND = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.(?:jpe?g|png|webp)")


class HeaderSettings(BaseModel):
    """The strip above every board (§12, header): greeting, clock, search, weather, stats.

    Instance-wide and admin-authored: every signed-in user sees what is named here, whatever
    their boards show — so name only what everyone may see.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    greeting: bool = True
    clock: bool = True
    # A preset name, a custom ``https://…{q}…`` URL, or "none".
    search: str = "duckduckgo"
    weather: str | None = None  # a weather resource uid, e.g. weather:current:home
    stats: list[HeroStat] = Field(default_factory=list, max_length=8)

    @field_validator("search")
    @classmethod
    def _search(cls, v: str) -> str:
        if v in SEARCH_ENGINES or v == "none":
            return v
        if v.startswith("https://") and "{q}" in v:
            return v
        names = ", ".join([*SEARCH_ENGINES, "none"])
        msg = f"search must be one of {names}, or an https URL containing {{q}}"
        raise ValueError(msg)

    @property
    def search_url(self) -> str | None:
        return None if self.search == "none" else SEARCH_ENGINES.get(self.search, self.search)


class Appearance(BaseModel):
    """Opt-in backdrop (invariant 9 amendment, 2026-09-27): an image of the admin's choosing
    behind the boards, dimmed and optionally blurred, with translucent cards. Surfaces
    only; ``--status-*`` never changes. Off unless ``background`` names a file."""

    model_config = ConfigDict(extra="forbid")

    background: str | None = None  # a file name in /config/backgrounds/
    # Percent of the page colour laid over it. At least 50: any image, even a white one,
    # must leave text readable (text straight on the backdrop also gets a halo).
    dim: int = Field(default=60, ge=50, le=95)
    blur: int = Field(default=0, ge=0, le=40)  # px
    translucent: bool = True  # cards let the backdrop through (only with a background)

    @field_validator("background")
    @classmethod
    def _file_name(cls, v: str | None) -> str | None:
        if v is not None and not _BACKGROUND.fullmatch(v):
            msg = (
                "background must be a file name in /config/backgrounds/ "
                "(.jpg, .png or .webp), not a path or URL"
            )
            raise ValueError(msg)
        return v


THEME_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


class ThemePark(BaseModel):
    """A theme.park palette for HUD's surfaces and text (PLAN §8.6). The backend fetches
    ``{source}/css/theme-options/{theme}.css`` (or the community folder) and serves it as
    HUD's own stylesheet, so the browser never loads a third-party one. Status colours are
    never themed (invariant 9). Added 2026-09-29, optional."""

    model_config = ConfigDict(extra="forbid")

    source: str  # a theme.park instance's base URL, e.g. http://theme-park:80
    # A theme name (organizr-contrast, nord, catppuccin-mocha). With `follow`, the one used
    # while the picker cannot be asked.
    theme: str | None = None
    # A theme picker's current-theme endpoint (JSON with a "theme" key), so HUD wears
    # whatever the rest of the stack wears, e.g. http://theme-picker:8090/api/current
    follow: str | None = None
    refresh: str = "1m"  # how often the theme is asked for and fetched again

    @field_validator("source", "follow")
    @classmethod
    def _http(cls, v: str | None) -> str | None:
        if v is None:
            return v
        parts = urlsplit(v)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.query:
            msg = "theme_park URLs must be http(s), e.g. http://theme-park:80"
            raise ValueError(msg)
        return v.rstrip("/")

    @field_validator("theme")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        if v is not None and not THEME_NAME.fullmatch(v):
            msg = "theme_park.theme must be a theme name like nord or organizr-contrast"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _which(self) -> Self:
        if self.theme is None and self.follow is None:
            msg = "theme_park needs a theme, a follow URL, or both"
            raise ValueError(msg)
        return self

    @field_validator("refresh")
    @classmethod
    def _dur(cls, v: str) -> str:
        if not 60 <= parse_duration(v) <= 86_400:
            msg = "theme_park.refresh must be between 1m and 24h"
            raise ValueError(msg)
        return v


class SettingsSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str = "HUD"
    timezone: str = "UTC"
    theme: Literal["dark", "light", "auto"] = "dark"
    retention: Retention = Retention()
    auth: AuthSettings = AuthSettings()
    header: HeaderSettings = HeaderSettings()
    appearance: Appearance = Appearance()
    theme_park: ThemePark | None = None

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"unknown IANA timezone {v!r}"
            raise ValueError(msg) from exc
        return v


class SettingsDocument(Document):
    kind: Literal["Settings"]
    spec: SettingsSpec = SettingsSpec()


DEFAULT_SETTINGS_YAML = """\
# HUD settings — written on first start because /config was empty.
# Everything here is optional; delete a key to fall back to its default.
apiVersion: hud/v1
kind: Settings
spec:
  title: HUD
  timezone: UTC          # IANA name, e.g. America/Chicago
  theme: dark            # dark | light | auto
  header:                # the strip above every board; everyone signed in sees it
    greeting: true
    clock: true
    search: duckduckgo   # duckduckgo | google | bing | startpage | kagi | brave | none | https://…{q}
    # weather: weather:current:home   # a weather provider's resource (providers/weather.yaml)
    # stats:
    #   - { resource: glances:host:main, metric: cpu_percent, label: CPU }
  # theme_park:          # a theme.park palette for surfaces and text; status colours stay
  #   source: http://theme-park:80
  #   theme: nord                                  # or, to wear the stack's current theme:
  #   follow: http://theme-picker:8090/api/current
  # appearance:          # opt-in backdrop: put the image in /config/backgrounds/
  #   background: wallpaper.jpg
  #   dim: 60            # 50-95, percent of the page colour laid over the image
  #   blur: 0            # px
  retention:             # how long each tier of history is kept (PLAN §6.2)
    samples: 48h
    rollup_5m: 14d
    rollup_1h: 180d
    rollup_1d: forever
  auth:                  # PLAN §10.1 — any of local, forward, oidc; order is precedence
    backends: [local]    # local: first visit creates the admin account; no default credentials
    session:
      cookie_name: hud_session
      lifetime: 7d
    # forward:           # trust identity headers from a proxy doing SSO (Authelia, Authentik, ...)
    #   trusted_proxies: ["172.20.0.0/16"]
    #   header_user: Remote-User
    #   header_groups: Remote-Groups
    # oidc:              # SSO without a forward-auth proxy
    #   discovery_url: https://id.example/.well-known/openid-configuration
    #   client_id: hud
    #   client_secret: ${secret:oidc_client_secret}
    #   groups_claim: groups
"""
