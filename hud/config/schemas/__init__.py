# SPDX-License-Identifier: Apache-2.0
"""``kind`` → document schema. Report arrives with the phase that implements it."""

from hud.config.schemas.base import API_VERSION, Document, Metadata
from hud.config.schemas.board import (
    BoardDocument,
    BoardSpec,
    EmbedWidget,
    ListWidget,
    MetricWidget,
    ResourceWidget,
    StaticWidget,
    UnsupportedWidget,
    Widget,
)
from hud.config.schemas.duration import parse_duration
from hud.config.schemas.provider import ProviderDocument, ProviderSpec, ResourceSpec
from hud.config.schemas.rbac import DEFAULT_RBAC_YAML, RbacDocument, RbacSpec
from hud.config.schemas.report import ReportDocument
from hud.config.schemas.settings import (
    DEFAULT_SETTINGS_YAML,
    AuthSettings,
    Retention,
    SettingsDocument,
    SettingsSpec,
)

KIND_SCHEMAS: dict[str, type[Document]] = {
    "Settings": SettingsDocument,
    "Provider": ProviderDocument,
    "Board": BoardDocument,
    "RBAC": RbacDocument,
    "Report": ReportDocument,
}

__all__ = [
    "API_VERSION",
    "DEFAULT_RBAC_YAML",
    "DEFAULT_SETTINGS_YAML",
    "KIND_SCHEMAS",
    "AuthSettings",
    "BoardDocument",
    "BoardSpec",
    "Document",
    "EmbedWidget",
    "ListWidget",
    "Metadata",
    "MetricWidget",
    "ProviderDocument",
    "ProviderSpec",
    "RbacDocument",
    "RbacSpec",
    "ReportDocument",
    "ResourceSpec",
    "ResourceWidget",
    "Retention",
    "SettingsDocument",
    "SettingsSpec",
    "StaticWidget",
    "UnsupportedWidget",
    "Widget",
    "parse_duration",
]
