# SPDX-License-Identifier: Apache-2.0
"""Connect a service (PLAN §8.5, round 3): the catalog of bundled templates, containers on
this host that match one, a connection test, and the provider file a connection writes.

The wizard is an editor over ordinary files. Connecting writes
``/config/providers/<name>.yaml`` — the template, its placeholders filled with what the user
typed (the base URL and any other plain setting) — and puts each credential in the secret
store, the file holding only ``${secret:name}``. Nothing here is wizard-only: the result is
exactly the file a person could have written by hand.

Every URL the wizard is asked to reach passes :func:`guard_url` first: no link-local or
cloud-metadata address, and not HUD itself. A connection test polls the template's first
resource group once, through the same engine and HTTP client a running provider uses.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from hud.collector import LiveCache, ResourceFilter
from hud.config.errors import ConfigError
from hud.config.loader import parse_yaml
from hud.config.schemas.provider import ProviderDocument, Requirement
from hud.config.secrets import SecretResolver
from hud.providers import ProviderContext
from hud.providers.declarative import build_declarative
from hud.providers.errors import ProviderError

TEMPLATES_DIR = (
    Path(os.environ.get("HUD_TEMPLATES_DIR", Path(__file__).resolve().parent.parent / "templates"))
    / "providers"
)
TEST_TIMEOUT = 15.0
_PLACEHOLDER = "${{{name}}}"
# 169.254.0.0/16 and fe80::/10 hold every cloud's metadata service; nothing a homelab
# service listens on lives there.
_METADATA_HOSTS = {"metadata.google.internal", "metadata"}


# ----------------------------------------------------------------------------- catalog


@dataclass(frozen=True)
class Template:
    name: str
    service: str
    icon: str | None
    docs: str | None
    requires: list[Requirement]
    images: list[str]
    port: int | None
    text: str

    def env(self) -> list[Requirement]:
        return [r for r in self.requires if r.kind == "env"]

    def secrets(self) -> list[Requirement]:
        return [r for r in self.requires if r.kind == "secret"]


def load_catalog(directory: Path = TEMPLATES_DIR) -> dict[str, Template]:
    """Every bundled declarative template, by provider name. A broken one is skipped:
    the template tests guard the bundled set; a missing directory is an empty catalog."""
    out: dict[str, Template] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        try:
            doc = ProviderDocument.model_validate(parse_yaml(text, path))
        except (ValueError, ConfigError):
            continue
        info = doc.metadata.template
        if info is None or doc.spec.plugin:
            continue
        out[doc.metadata.name] = Template(
            name=doc.metadata.name,
            service=info.service,
            icon=info.icon,
            docs=info.docs,
            requires=list(info.requires),
            images=list(info.images),
            port=info.port,
            text=text,
        )
    return out


def image_word(image: str) -> str:
    """The name part of an image reference: lscr.io/linuxserver/sonarr:latest → sonarr."""
    last = image.rsplit("/", 1)[-1]
    return last.split("@", 1)[0].split(":", 1)[0].lower()


def match_image(image: str, catalog: Mapping[str, Template]) -> Template | None:
    word = image_word(image)
    for t in catalog.values():
        if word == t.name or word in t.images:
            return t
    return None


# ----------------------------------------------------------------------------- rendering


def render(template: Template, values: Mapping[str, str]) -> str:
    """The provider file: the template with each plain placeholder filled in. Secrets stay
    ``${secret:name}`` references. Raises ValueError naming a missing or unusable value."""
    text = template.text
    for req in template.env():
        value = (values.get(req.name) or "").strip()
        if not value:
            msg = f"{req.name} is required: {req.hint}"
            raise ValueError(msg)
        if "\n" in value or "${" in value or '"' in value:
            msg = f"{req.name} must be one plain line"
            raise ValueError(msg)
        if req.name.endswith("_URL"):
            value = check_url(value)
        text = text.replace(_PLACEHOLDER.format(name=req.name), value)
    stamp = datetime.now(UTC).date().isoformat()
    header = (
        f"# Connected with HUD's Connect a service wizard on {stamp}, from the bundled\n"
        f"# {template.name} template. Credentials are in the secret store, never here.\n"
    )
    return header + text


def check_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        msg = "the base URL must be http(s)://host[:port]"
        raise ValueError(msg)
    if parts.query or parts.fragment or parts.username or parts.password:
        msg = "the base URL must not carry a query, fragment or credentials"
        raise ValueError(msg)
    return url.rstrip("/")


async def guard_url(url: str, own_port: int) -> None:
    """Refuse addresses a service never lives at: link-local (every cloud metadata
    service), unspecified, and HUD itself. Private and loopback addresses are a homelab's
    normal and stay allowed."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in _METADATA_HOSTS:
        msg = f"{host} is a cloud metadata address, not a service"
        raise ValueError(msg)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        addrs = [ipaddress.ip_address(host)]  # a literal is judged as written
    except ValueError:
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, port)
        except OSError as exc:
            msg = f"cannot resolve {host}: {exc.strerror or exc}"
            raise ValueError(msg) from exc
        addrs = [ipaddress.ip_address(info[4][0]) for info in infos]
    for addr in addrs:
        if addr.is_link_local or addr.is_unspecified:
            msg = f"{host} resolves to {addr}, a link-local or unspecified address"
            raise ValueError(msg)
        if addr.is_loopback and port == own_port:
            msg = "that is HUD itself"
            raise ValueError(msg)


# ----------------------------------------------------------------------------- testing


class _Typed(SecretResolver):
    """The secrets the user just typed first, then the store — so a test uses what they
    entered without writing anything."""

    def __init__(self, base: SecretResolver, typed: Mapping[str, str]) -> None:
        super().__init__(Path("/nonexistent"), Path("/nonexistent"), env={})
        self._base = base
        self._typed = {k: v.strip() for k, v in typed.items() if v and v.strip()}

    def resolve(self, name: str) -> str:
        return self._typed[name] if name in self._typed else self._base.resolve(name)

    def source(self, name: str) -> str | None:
        return "typed" if name in self._typed else self._base.source(name)


@dataclass
class ProbeResult:
    ok: bool
    seconds: float
    resources: int = 0
    sample: list[str] = field(default_factory=list)  # a few resource names, for "found: …"
    error: str | None = None


async def try_connection(
    template: Template, text: str, secrets: SecretResolver, typed: Mapping[str, str]
) -> ProbeResult:
    """Build the provider from ``text`` and poll its first resource group once."""
    started = time.monotonic()
    try:
        doc = ProviderDocument.model_validate(parse_yaml(text, Path(f"{template.name}.yaml")))
        ctx = ProviderContext.create(doc.metadata.name, _Typed(secrets, typed), {})
        provider = build_declarative(doc, ctx)
    except (ValueError, ConfigError, ProviderError) as exc:
        return ProbeResult(False, 0.0, error=_plain(exc))
    first = provider.groups()[0].name
    await provider.startup()
    try:
        result = await asyncio.wait_for(provider.poll(first), TEST_TIMEOUT)
    except TimeoutError:
        return ProbeResult(False, time.monotonic() - started, error="no answer in 15 s")
    except (ProviderError, OSError, ValueError) as exc:
        return ProbeResult(False, time.monotonic() - started, error=_plain(exc))
    finally:
        await provider.shutdown()
    names = [r.name for r in result.resources[:3]]
    return ProbeResult(True, time.monotonic() - started, len(result.resources), names)


def _plain(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    return re.sub(r"\s+", " ", text)[:300]


# ----------------------------------------------------------------------------- discovery


@dataclass
class Found:
    container: str
    image: str
    template: str
    service: str
    connected: bool
    reachable: bool | None  # None: HUD's own networks are unknown
    suggested_url: str | None
    note: str | None = None


def own_container(cache: LiveCache) -> dict[str, Any] | None:
    """HUD's own container, if the Docker provider lists it: its id starts with this
    host's name (Docker's default hostname), or it runs a HUD image."""
    host = socket.gethostname()
    for r in cache.resources(ResourceFilter(kind=["container"])):
        cid = str(r.attrs.get("id", ""))
        if (cid and host.startswith(cid[:12])) or image_word(str(r.attrs.get("image", ""))) in (
            "homelabhud",
            "homelab-hud",
        ):
            return {"name": r.name, **r.attrs}
    return None


def discover(cache: LiveCache, catalog: Mapping[str, Template], connected: set[str]) -> list[Found]:
    """Containers on this host that a bundled template can connect, not-yet-connected first."""
    me = own_container(cache)
    mine = set(me.get("networks") or []) if me else None
    out: list[Found] = []
    for r in cache.resources(ResourceFilter(kind=["container"])):
        image = str(r.attrs.get("image") or "")
        t = match_image(image, catalog)
        if t is None:
            continue
        nets = set(r.attrs.get("networks") or [])
        reachable = None if mine is None else bool(nets & mine)
        url, note = _suggest(r.name, t, r.attrs.get("ports") or [], reachable)
        out.append(
            Found(r.name, image, t.name, t.service, t.name in connected, reachable, url, note)
        )
    return sorted(out, key=lambda f: (f.connected, f.service.lower(), f.container))


def _suggest(
    name: str, t: Template, ports: list[str], reachable: bool | None
) -> tuple[str | None, str | None]:
    if reachable is not False and t.port:
        return f"http://{name}:{t.port}", None
    published = next(
        (
            p.split("->")[0]
            for p in ports
            if "->" in p and p.split("->")[1].startswith(f"{t.port}/")
        ),
        None,
    )
    note = "not on a Docker network HUD shares; use the host's address"
    if published:
        return f"http://<this-server>:{published}", note
    return None, (note if reachable is False else None)


__all__ = [
    "Found",
    "ProbeResult",
    "Template",
    "check_url",
    "discover",
    "guard_url",
    "image_word",
    "load_catalog",
    "match_image",
    "render",
    "try_connection",
]
