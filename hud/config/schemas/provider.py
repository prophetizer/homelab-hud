# SPDX-License-Identifier: Apache-2.0
"""``kind: Provider`` — a declarative (Tier 1) provider (PLAN.md §7.1).

The spec surface is deliberately small: ``transport``, ``defaults``, ``resources[]`` and
``actions[]``. Expressions are strings here; they are compiled by the declarative engine.
Two things are enforced at *config* time because getting them wrong later is expensive:

* Credentials in ``transport.auth`` must be ``${secret:name}`` references (invariant 3).
* ``map.uid`` must be ``{provider}:{kind}:<template>`` and the template must not end in a
  field known to be volatile, or the resource's history forks on every recreate (§5).
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hud.config.schemas.base import Document, Metadata
from hud.config.schemas.settings import parse_duration
from hud.config.secrets import is_secret_ref

HttpMethod = Literal["GET", "POST"]

# Field names that are content hashes or per-instance ids. A uid template whose last
# ``item.<field>`` reference is one of these is refused with a pointer to the name field.
VOLATILE_UID_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "Id",
        "ID",
        "hash",
        "digest",
        "sha",
        "pid",
        "ImageID",
        "ContainerID",
        "image_id",
        "container_id",
    }
)

_ITEM_REF = re.compile(r"item((?:\.[A-Za-z_][A-Za-z0-9_]*|\[['\"][^'\"]+['\"]\])+)")
# A native id with no item reference is a singleton id; it must be a plain token, and must
# not be a Jinja expression that merely happens to reference nothing from the item.
_EXPR_MARKER = re.compile(r"\{\{|\{%")
_LITERAL_NATIVE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-/]*")
_LAST_SEG = re.compile(r"(?:\.([A-Za-z_][A-Za-z0-9_]*)|\[['\"]([^'\"]+)['\"]\])$")


def _duration(v: str) -> str:
    parse_duration(v)
    return v


class _Spec(BaseModel):
    """Every nested block preserves unknown keys (warned, never dropped — PLAN.md §11.3)."""

    model_config = ConfigDict(extra="allow")


# ----------------------------------------------------------------------------- transport


class AuthNone(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["none"] = "none"


class AuthBearer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["bearer"]
    token: str

    @field_validator("token")
    @classmethod
    def _ref(cls, v: str) -> str:
        return _must_be_secret_ref("token", v)


class AuthApiKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["api_key"]
    header: str = "X-Api-Key"
    key: str

    @field_validator("key")
    @classmethod
    def _ref(cls, v: str) -> str:
        return _must_be_secret_ref("key", v)


class AuthBasic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["basic"]
    username: str
    password: str

    @field_validator("password")
    @classmethod
    def _ref(cls, v: str) -> str:
        return _must_be_secret_ref("password", v)


class AuthHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["header"]
    name: str
    value: str

    @field_validator("value")
    @classmethod
    def _ref(cls, v: str) -> str:
        return _must_be_secret_ref("value", v)


class AuthQuery(BaseModel):
    """A secret sent as a query parameter, for APIs that accept nothing else (SABnzbd's
    ``apikey``; Jellyfin's ``ApiKey``, the one form not behind its legacy switch). Added
    2026-09-25; a new auth type is dashboard/v1-compatible (§11.3a). A URL is where secrets
    leak, so the value is masked in every log line and provider error (hud.config.redact)
    — but the target app's own access log is beyond HUD's reach: point base_url at the
    container, not through a reverse proxy that logs query strings."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["query"]
    param: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    value: str

    @field_validator("value")
    @classmethod
    def _ref(cls, v: str) -> str:
        return _must_be_secret_ref("value", v)


def _must_be_secret_ref(field: str, v: str) -> str:
    if not is_secret_ref(v):
        msg = f"{field} must be a ${{secret:<name>}} reference, never a literal credential"
        raise ValueError(msg)
    return v


Auth = Annotated[
    AuthNone | AuthBearer | AuthApiKey | AuthBasic | AuthHeader | AuthQuery,
    Field(discriminator="type"),
]


class Transport(_Spec):
    base_url: str
    auth: Auth = AuthNone()
    timeout: str = "10s"
    verify_tls: bool = True
    # Optional per-host rate limit: max concurrent in-flight requests to this base_url.
    max_connections: int = Field(default=4, ge=1, le=64)

    @field_validator("timeout")
    @classmethod
    def _dur(cls, v: str) -> str:
        return _duration(v)

    @field_validator("base_url")
    @classmethod
    def _base_url_shape(cls, v: str) -> str:
        # ${VAR} is substituted at instantiation; after that it must be absolute http(s).
        if "${" in v:
            return v
        if not re.match(r"^https?://[^/\s]+", v):
            msg = "base_url must be an absolute http(s) URL (or contain ${VAR} references)"
            raise ValueError(msg)
        return v

    @property
    def timeout_seconds(self) -> int:
        return parse_duration(self.timeout)


class Defaults(_Spec):
    interval: str = "30s"
    jitter: str = "5s"

    @field_validator("interval", "jitter")
    @classmethod
    def _dur(cls, v: str) -> str:
        return _duration(v)

    @property
    def interval_seconds(self) -> int:
        return parse_duration(self.interval)

    @property
    def jitter_seconds(self) -> int:
        return parse_duration(self.jitter)


# ----------------------------------------------------------------------------- resources


class Request(_Spec):
    method: HttpMethod = "GET"
    path: str
    params: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] | None = None

    @field_validator("path")
    @classmethod
    def _relative(cls, v: str) -> str:
        # The only outbound host a Tier 1 provider may reach is transport.base_url (R5).
        if re.match(r"^[a-z][a-z0-9+.-]*://", v, re.IGNORECASE) or v.startswith("//"):
            msg = "path must be relative to transport.base_url, not an absolute URL"
            raise ValueError(msg)
        return v


class Paginate(_Spec):
    style: Literal["page", "cursor", "offset"]
    # page:   ?<param>=1,2,3…   until an empty page or max_pages
    # offset: ?<param>=0,N,2N…  with size_param=N
    # cursor: read next cursor from response at `cursor_path`, send as <param>
    param: str
    size_param: str | None = None
    size: int = Field(default=100, ge=1, le=10_000)
    cursor_path: str | None = None  # JSONPath to the next-cursor value in the response
    max_pages: int = Field(default=20, ge=1, le=1000)

    @model_validator(mode="after")
    def _style_fields(self) -> Self:
        if self.style == "cursor" and not self.cursor_path:
            msg = "cursor pagination requires cursor_path"
            raise ValueError(msg)
        if self.style == "offset" and not self.size_param:
            msg = "offset pagination requires size_param"
            raise ValueError(msg)
        return self


class MetricMap(_Spec):
    name: str
    value: str  # expression → float
    when: str | None = None  # expression → truthy; metric skipped when false
    unit: str | None = None  # literal SourceUnit name
    unit_from: str | None = None  # expression → provider unit string, mapped via alias table

    @model_validator(mode="after")
    def _one_unit(self) -> Self:
        if (self.unit is None) == (self.unit_from is None):
            msg = "exactly one of unit or unit_from is required"
            raise ValueError(msg)
        return self


class ResourceMap(_Spec):
    uid: str
    kind: str
    name: str
    state: str = "unknown"
    attrs: dict[str, str] = Field(default_factory=dict)
    links: dict[str, str] = Field(default_factory=dict)
    parent_uid: str | None = None

    @field_validator("kind")
    @classmethod
    def _kind_literal(cls, v: str) -> str:
        # kind is part of the uid prefix and of every widget selector; it stays static so
        # a provider's resource kinds are knowable from its YAML alone.
        if "{{" in v or not re.fullmatch(r"[A-Za-z0-9_-]+", v):
            msg = "kind must be a literal identifier, not an expression"
            raise ValueError(msg)
        return v


class ResourceSpec(_Spec):
    name: str
    request: Request
    select: str = "$"
    map: ResourceMap
    metrics: list[MetricMap] = Field(default_factory=list)
    interval: str | None = None
    jitter: str | None = None
    paginate: Paginate | None = None

    @field_validator("interval", "jitter")
    @classmethod
    def _dur(cls, v: str | None) -> str | None:
        return v if v is None else _duration(v)


# ----------------------------------------------------------------------------- actions


class ActionApplies(_Spec):
    kind: str


class ActionSpec(_Spec):
    """Parsed and validated so the spec is complete; nothing executes it before Phase 3."""

    verb: str
    applies_to: ActionApplies
    request: Request
    confirm: bool = True
    required_permission: str


# ----------------------------------------------------------------------------- document


class Requirement(BaseModel):
    """One placeholder the user must supply before a template works."""

    model_config = ConfigDict(extra="forbid")

    name: str  # env var name, or secret name for kind: secret
    kind: Literal["env", "secret"]
    hint: str


class TemplateInfo(BaseModel):
    """Display metadata a bundled template carries (PLAN.md §8.5.1). Harmless on a
    hand-written provider; the wizard catalog reads it in Phase 3."""

    model_config = ConfigDict(extra="forbid")

    service: str
    icon: str | None = None
    docs: str | None = None
    requires: list[Requirement] = Field(default_factory=list)


class ProviderMetadata(Metadata):
    name: str
    labels: dict[str, str] = Field(default_factory=dict)
    template: TemplateInfo | None = None

    @field_validator("name")
    @classmethod
    def _name_shape(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", v):
            msg = "provider name must be lowercase [a-z0-9_-] (it is the uid prefix)"
            raise ValueError(msg)
        return v


class ProviderSpec(_Spec):
    """Either tier, one envelope.

    * Tier 1 (declarative): ``transport`` + ``resources[]``.
    * Tier 2 (plugin): ``plugin`` (its registered name) + ``config`` (validated by the
      plugin's own config model at build time; ``${secret:}`` and ``${VAR}`` apply).
    """

    transport: Transport | None = None
    defaults: Defaults = Defaults()
    resources: list[ResourceSpec] = Field(default_factory=list)
    actions: list[ActionSpec] = Field(default_factory=list)
    plugin: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_plugin(self) -> bool:
        return self.plugin is not None

    @field_validator("plugin")
    @classmethod
    def _plugin_name(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", v):
            msg = "plugin name must be lowercase [a-z0-9_-]"
            raise ValueError(msg)
        return v

    @field_validator("resources")
    @classmethod
    def _unique_names(cls, v: list[ResourceSpec]) -> list[ResourceSpec]:
        names = [r.name for r in v]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            msg = f"duplicate resource names: {', '.join(dupes)}"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _one_tier(self) -> Self:
        if self.plugin is not None:
            if self.transport is not None or self.resources:
                msg = "a plugin provider takes plugin + config; transport/resources are Tier 1"
                raise ValueError(msg)
            return self
        if self.transport is None:
            msg = "transport is required (or set plugin for a Tier 2 provider)"
            raise ValueError(msg)
        if not self.resources:
            msg = "resources must list at least one entry"
            raise ValueError(msg)
        if self.config:
            msg = "config applies to plugin providers only"
            raise ValueError(msg)
        return self


class ProviderDocument(Document):
    kind: Literal["Provider"]
    metadata: ProviderMetadata
    spec: ProviderSpec

    @model_validator(mode="after")
    def _uid_templates(self) -> Self:
        for i, res in enumerate(self.spec.resources):
            problem = check_uid_template(self.metadata.name, res.map.kind, res.map.uid)
            if problem:
                msg = f"spec.resources.{i}.map.uid: {problem}"
                raise ValueError(msg)
        return self


def _check_singleton_native_id(native: str) -> str | None:
    """A native id that references no item field belongs to a *singleton* resource — the
    service itself, from a single-object endpoint. A literal is the most stable id
    available, so it is accepted; the volatility rule guards the opposite failure, and the
    one risk a constant carries (every item of a list collapsing onto one uid) is caught
    per poll by the declarative engine's duplicate-uid check."""
    if _EXPR_MARKER.search(native):
        return (
            "native id is computed but references no item field; use a literal for a "
            "singleton resource, or {{ item.<field> }} for one resource per item"
        )
    if not _LITERAL_NATIVE_ID.fullmatch(native):
        return (
            f"literal native id {native!r} must be [A-Za-z0-9] with ._-/ inside "
            "(it is a singleton id, e.g. 'main')"
        )
    return None


def check_uid_template(provider: str, kind: str, template: str) -> str | None:
    """Return a problem description, or None when the template is acceptable."""
    prefix = f"{provider}:{kind}:"
    if not template.startswith(prefix):
        return f"must start with {prefix!r} (provider name and literal kind)"
    native = template.removeprefix(prefix)
    if not native.strip():
        return "native id part is empty"
    refs = _ITEM_REF.findall(native)
    if not refs:
        return _check_singleton_native_id(native)
    m = _LAST_SEG.search(refs[-1])
    last = (m.group(1) or m.group(2)) if m else None
    if last in VOLATILE_UID_FIELDS:
        return (
            f"derives from item.{last}, which is volatile; use a stable field such as the "
            "name (invariant 8: an unstable uid forks the resource's history)"
        )
    return None
