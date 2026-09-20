# SPDX-License-Identifier: Apache-2.0
"""``settings.yaml`` — global options (PLAN.md §11.2). Phase 0 carries only what exists."""

import ipaddress
import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from hud.config.schemas.base import Document
from hud.config.secrets import is_secret_ref

_DURATION = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> int:
    """``'48h'`` → seconds. Also accepts ``'forever'`` → 0 (meaning no expiry)."""
    if text == "forever":
        return 0
    m = _DURATION.fullmatch(text.strip())
    if m is None:
        msg = f"invalid duration {text!r}; use <n>s|m|h|d or 'forever'"
        raise ValueError(msg)
    return int(m.group(1)) * _UNIT_SECONDS[m.group(2)]


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


class SettingsSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str = "HUD"
    timezone: str = "UTC"
    theme: Literal["dark", "light", "auto"] = "dark"
    retention: Retention = Retention()
    auth: AuthSettings = AuthSettings()

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
